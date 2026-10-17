"""Hybrid price engine (formula version v2).

v2 refinements: engagement (like-rate change on recent uploads) and popularity momentum
inside the fundamental score, exponentially time-decayed order flow with a liquidity
confidence (thin markets move less), and a popularity-level term in sentiment.

raw_return   = 0.50 * fundamental_return + 0.30 * flow_return + 0.20 * sentiment_return
daily_return = clamp(raw_return, -0.08, +0.08)
next_price   = max(MIN_PRICE, round(base_price * (1 + daily_return)))

fundamental_return ∈ [-0.04, 0.04], flow_return ∈ [-0.03, 0.03], sentiment_return ∈ [-0.02, 0.02].
base_price is the asset's official reference price (the previous daily close), which is
manipulation-resistant; intraday trades influence the next price only through flow_return.
"""

import logging
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.config import get_settings
from app.models import (
    Channel,
    ChannelMetric,
    MarketAsset,
    MarketEvent,
    PriceUpdate,
    Trade,
    User,
)
from app.security import utcnow
from app.services import bars
from app.services.audit import write_audit

log = logging.getLogger("yex.pricing")

FORMULA_VERSION = "hybrid-v2"
WEIGHTS = {"fundamental": Decimal("0.50"), "flow": Decimal("0.30"), "sentiment": Decimal("0.20")}
LIMITS = {"fundamental": 0.04, "flow": 0.03, "sentiment": 0.02}
DAILY_CLAMP = Decimal("0.08")
# v2 adds engagement (like-rate change on recent uploads) and popularity momentum.
FUNDAMENTAL_MIX = {"subscribers": 0.30, "views": 0.30, "trend": 0.15, "engagement": 0.15, "popularity": 0.10}
LIKE_RATE_SCALE = 0.005  # +0.5%p like rate in a week ≈ tanh(1)
POPULARITY_SCALE = 5.0  # +5 popularity points in a week ≈ tanh(1)
POPULARITY_LEVEL_SCALE = 10.0
POPULARITY_SENTIMENT_WEIGHT = 0.3
FLOW_HALF_LIFE_HOURS = 6.0  # recent executions count more than old ones
FLOW_LIQUIDITY_FRACTION = 0.0005  # turnover of 0.05% of market cap → full flow confidence
SUB_SCALE = 0.01  # 1%/week relative sub growth ≈ tanh(1) score
VIEW_SCALE = 0.03
TREND_SCALE = 0.01
GROWTH_CAP = 0.5  # winsorise outliers (e.g. data glitches, viral explosions)
FLOW_EPSILON = Decimal(1)
FLOW_FULL_CONFIDENCE_TRADERS = 5
METRIC_TOLERANCE_DAYS = 2


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


@dataclass
class Features:
    has_7d: bool
    has_28d: bool
    hidden_subs: bool
    sub_g7: float | None
    sub_g28w: float | None
    view_g7: float | None
    view_g28w: float | None
    view_g7_prev: float | None  # growth of the week before (days -14..-7)
    confidence: float
    like_rate: float | None = None  # likes / views over recent uploads, today
    like_rate_prev: float | None = None  # same, 7 days ago


def _series(db: Session, channel: Channel, metric_type: str, start: date) -> dict[date, int]:
    rows = db.execute(
        select(ChannelMetric.observed_date, ChannelMetric.metric_value).where(
            ChannelMetric.channel_id == channel.id,
            ChannelMetric.metric_type == metric_type,
            ChannelMetric.source == channel.data_source,
            ChannelMetric.observed_date >= start,
        )
    ).all()
    return {d: v for d, v in rows}


def _value_at(series: dict[date, int], day: date) -> int | None:
    for back in range(METRIC_TOLERANCE_DAYS + 1):
        v = series.get(day - timedelta(days=back))
        if v is not None:
            return v
    return None


def _growth(now: int | None, then: int | None) -> float | None:
    if now is None or then is None or then <= 0:
        return None
    return clamp((now - then) / then, -GROWTH_CAP, GROWTH_CAP)


def compute_features(db: Session, channel: Channel, as_of: date) -> Features:
    start = as_of - timedelta(days=28 + METRIC_TOLERANCE_DAYS + 1)
    subs = _series(db, channel, "subscriber_count", start)
    views = _series(db, channel, "view_count", start)
    s0, s7, s28 = (_value_at(subs, as_of - timedelta(days=d)) for d in (0, 7, 28))
    v0, v7, v14, v28 = (_value_at(views, as_of - timedelta(days=d)) for d in (0, 7, 14, 28))
    sub_g7, view_g7 = _growth(s0, s7), _growth(v0, v7)
    sub_g28 = _growth(s0, s28)
    view_g28 = _growth(v0, v28)
    likes = _series(db, channel, "recent_like_count", start)
    rviews = _series(db, channel, "recent_view_count", start)

    def like_rate(day: date) -> float | None:
        lk, vw = _value_at(likes, day), _value_at(rviews, day)
        return lk / vw if lk is not None and vw else None

    has_7d = view_g7 is not None
    has_28d = view_g28 is not None
    hidden = channel.hidden_subscriber_count
    confidence = 0.0
    if has_7d:
        confidence = 1.0 if has_28d else 0.5
        if hidden or sub_g7 is None:
            confidence *= 0.8
    return Features(
        has_7d=has_7d,
        has_28d=has_28d,
        hidden_subs=hidden,
        sub_g7=sub_g7,
        sub_g28w=sub_g28 / 4 if sub_g28 is not None else None,
        view_g7=view_g7,
        view_g28w=view_g28 / 4 if view_g28 is not None else None,
        view_g7_prev=_growth(v7, v14),
        confidence=confidence,
        like_rate=like_rate(as_of),
        like_rate_prev=like_rate(as_of - timedelta(days=7)),
    )


def fundamental_return(f: Features, market_sub_median: float, market_view_median: float,
                       pop_now: int | None = None, pop_prev: int | None = None) -> tuple[float, dict]:
    """Growth relative to the market median (keeps the market roughly zero-sum on fundamentals)."""
    if not f.has_7d:
        return 0.0, {"reason": "insufficient_history"}
    sub_score = (
        math.tanh((f.sub_g7 - market_sub_median) / SUB_SCALE) if f.sub_g7 is not None else 0.0
    )
    view_score = math.tanh(((f.view_g7 or 0.0) - market_view_median) / VIEW_SCALE)
    trend_score = 0.0
    if f.has_28d:
        parts = []
        if f.sub_g7 is not None and f.sub_g28w is not None:
            parts.append(f.sub_g7 - f.sub_g28w)
        if f.view_g7 is not None and f.view_g28w is not None:
            parts.append((f.view_g7 - f.view_g28w) / 3)
        if parts:
            trend_score = math.tanh(sum(parts) / len(parts) / TREND_SCALE)
    engagement_score = None
    if f.like_rate is not None and f.like_rate_prev is not None:
        engagement_score = math.tanh((f.like_rate - f.like_rate_prev) / LIKE_RATE_SCALE)
    popularity_score = None
    if pop_now is not None and pop_prev is not None:
        popularity_score = math.tanh((pop_now - pop_prev) / POPULARITY_SCALE)
    parts = {"subscribers": sub_score if f.sub_g7 is not None else None, "views": view_score,
             "trend": trend_score, "engagement": engagement_score, "popularity": popularity_score}
    # Missing inputs (hidden subscribers/likes, no popularity history yet) give their weight
    # to the remaining components instead of counting as zero.
    mix = {k: w for k, w in FUNDAMENTAL_MIX.items() if parts[k] is not None}
    total_w = sum(mix.values())
    score = sum(w / total_w * parts[k] for k, w in mix.items())
    ret = clamp(LIMITS["fundamental"] * score * f.confidence, -LIMITS["fundamental"], LIMITS["fundamental"])
    return ret, {
        "sub_score": round(sub_score, 6),
        "view_score": round(view_score, 6),
        "trend_score": round(trend_score, 6),
        "engagement_score": None if engagement_score is None else round(engagement_score, 6),
        "popularity_score": None if popularity_score is None else round(popularity_score, 6),
        "weights": {k: round(w / total_w, 4) for k, w in mix.items()},
        "score": round(score, 6),
        "confidence": f.confidence,
    }


def flow_return(db: Session, asset_id: int, start: datetime, end: datetime,
                market_cap: int | None = None) -> tuple[float, dict]:
    """D = (buy - sell) / (buy + sell + eps) over executed trades in (start, end].

    Buy/sell is the aggressor (taker) side. Each trade counts once because successive
    windows never overlap. Unfilled orders are ignored. Trades where the system market
    maker was the aggressor are excluded, and confidence scales with distinct traders.
    """
    buyer = aliased(User)
    seller = aliased(User)
    taker_is_system = or_(
        and_(Trade.taker_side == "buy", buyer.role == "system"),
        and_(Trade.taker_side == "sell", seller.role == "system"),
    )
    notional = Trade.quantity * Trade.execution_price
    # Exponential time decay: an execution FLOW_HALF_LIFE_HOURS old counts half.
    decay = func.power(0.5, func.extract("epoch", end - Trade.executed_at) / (FLOW_HALF_LIFE_HOURS * 3600))
    q = (
        select(
            func.coalesce(func.sum(case((Trade.taker_side == "buy", notional), else_=0)), 0),
            func.coalesce(func.sum(case((Trade.taker_side == "sell", notional), else_=0)), 0),
            func.coalesce(func.sum(case((Trade.taker_side == "buy", notional * decay), else_=0)), 0),
            func.coalesce(func.sum(case((Trade.taker_side == "sell", notional * decay), else_=0)), 0),
            func.count(Trade.id),
            func.count(
                func.distinct(case((Trade.taker_side == "buy", Trade.buyer_id), else_=Trade.seller_id))
            ),
        )
        .join(buyer, buyer.id == Trade.buyer_id)
        .join(seller, seller.id == Trade.seller_id)
        .where(
            Trade.asset_id == asset_id,
            Trade.executed_at > start,
            Trade.executed_at <= end,
            ~taker_is_system,
        )
    )
    buy_n, sell_n, buy_w, sell_w, n_trades, n_traders = db.execute(q).one()
    buy_n, sell_n = Decimal(buy_n), Decimal(sell_n)
    buy_w, sell_w = Decimal(str(buy_w)), Decimal(str(sell_w))
    d = (buy_w - sell_w) / (buy_w + sell_w + FLOW_EPSILON)
    d = clamp(d, Decimal(-1), Decimal(1))
    trader_conf = min(1.0, n_traders / FLOW_FULL_CONFIDENCE_TRADERS)
    liquidity_conf = 1.0
    if market_cap:
        # Thin markets move the price less: confidence grows with sqrt(turnover / threshold).
        liquidity_conf = min(1.0, math.sqrt(float(buy_n + sell_n) / (FLOW_LIQUIDITY_FRACTION * market_cap)))
    conf = trader_conf * liquidity_conf
    ret = clamp(float(d) * LIMITS["flow"] * conf, -LIMITS["flow"], LIMITS["flow"])
    return ret, {
        "buy_notional": int(buy_n),
        "sell_notional": int(sell_n),
        "imbalance": float(d),
        "trades": n_trades,
        "distinct_takers": n_traders,
        "trader_confidence": trader_conf,
        "liquidity_confidence": round(liquidity_conf, 6),
        "confidence": conf,
    }


def sentiment_return(db: Session, asset_id: int, at: datetime, popularity: int | None = None,
                     popularity_median: float | None = None) -> tuple[float, dict]:
    events = db.execute(
        select(MarketEvent).where(
            MarketEvent.asset_id == asset_id,
            MarketEvent.observed_at <= at,
            MarketEvent.expires_at > at,
        )
    ).scalars().all()
    event_total = sum(float(e.event_score) for e in events)
    pop_term = 0.0
    if popularity is not None and popularity_median is not None:
        # Popular channels (vs. the market median) carry a mild positive mood, unpopular a negative one.
        pop_term = POPULARITY_SENTIMENT_WEIGHT * math.tanh((popularity - popularity_median) / POPULARITY_LEVEL_SCALE)
    total = clamp(event_total + pop_term, -1.0, 1.0)
    ret = clamp(total * LIMITS["sentiment"], -LIMITS["sentiment"], LIMITS["sentiment"])
    return ret, {
        "score": total,
        "popularity_term": round(pop_term, 6),
        "events": [{"id": e.id, "type": e.event_type, "score": float(e.event_score)} for e in events],
    }


def combine(fund: float, flow: float, sent: float) -> tuple[Decimal, Decimal]:
    raw = (
        WEIGHTS["fundamental"] * Decimal(str(fund))
        + WEIGHTS["flow"] * Decimal(str(flow))
        + WEIGHTS["sentiment"] * Decimal(str(sent))
    )
    return raw, clamp(raw, -DAILY_CLAMP, DAILY_CLAMP)


def next_price(base: int, daily: Decimal) -> int:
    p = (Decimal(base) * (1 + daily)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return max(get_settings().min_price, int(p))


def is_stale(channel: Channel, now: datetime) -> bool:
    if channel.last_data_update_at is None:
        return True
    return now - channel.last_data_update_at > timedelta(hours=get_settings().stale_after_hours)


def _market_medians(db: Session, as_of: date) -> tuple[float, float]:
    channels = db.execute(
        select(Channel).where(Channel.listing_status == "listed")
    ).scalars().all()
    subs, views = [], []
    for ch in channels:
        f = compute_features(db, ch, as_of)
        if f.sub_g7 is not None:
            subs.append(f.sub_g7)
        if f.view_g7 is not None:
            views.append(f.view_g7)
    # With very few listings a median is meaningless, so fall back to absolute growth.
    sub_m = statistics.median(subs) if len(subs) >= 3 else 0.0
    view_m = statistics.median(views) if len(views) >= 3 else 0.0
    return sub_m, view_m


def run_daily_update(db: Session, effective: date, now: datetime | None = None) -> dict:
    """Apply the daily price update for every listed asset. Safe to re-run: each
    (asset, effective_date) is processed at most once, enforced by a UNIQUE constraint."""
    from app.services.listing import auto_halt  # local import avoids a cycle

    now = now or utcnow()
    try:
        medians = _market_medians(db, effective)
    except Exception:  # per-asset updates below will record their own failures
        log.exception("market median computation failed; using absolute growth")
        medians = (0.0, 0.0)
    summary = {"applied": 0, "skipped": 0, "failed": 0, "already_done": 0}
    asset_ids = db.execute(
        select(MarketAsset.id)
        .join(Channel, Channel.id == MarketAsset.channel_id)
        .where(Channel.listing_status == "listed")
        .order_by(MarketAsset.id)
    ).scalars().all()
    for asset_id in asset_ids:
        try:
            with db.begin_nested():
                outcome = _update_asset(db, asset_id, effective, now, medians, auto_halt)
            summary[outcome] += 1
        except IntegrityError:
            summary["already_done"] += 1  # concurrent run inserted the same (asset, date)
        except Exception as exc:  # keep the old price; record the failure
            log.exception("price update failed asset=%s", asset_id)
            with db.begin_nested():
                asset = db.get(MarketAsset, asset_id)
                if asset is not None and not _exists(db, asset_id, effective):
                    db.add(
                        PriceUpdate(
                            asset_id=asset_id,
                            effective_date=effective,
                            status="failed",
                            old_price=asset.reference_price,
                            new_price=asset.reference_price,
                            formula_version=FORMULA_VERSION,
                            error=f"{type(exc).__name__}: {exc}"[:2000],
                        )
                    )
                    write_audit(db, "price.update_failed", target_type="asset", target_id=asset_id,
                                metadata={"effective_date": effective.isoformat(), "error": type(exc).__name__})
            summary["failed"] += 1
    db.flush()
    return summary


def _popularity_median(db: Session) -> float | None:
    scores = [s for s in db.execute(select(MarketAsset.popularity_score).join(
        Channel, Channel.id == MarketAsset.channel_id).where(
        Channel.listing_status == "listed", MarketAsset.popularity_score.is_not(None))).scalars()]
    return statistics.median(scores) if len(scores) >= 3 else None


def _exists(db: Session, asset_id: int, effective: date) -> bool:
    return db.execute(
        select(PriceUpdate.id).where(
            PriceUpdate.asset_id == asset_id, PriceUpdate.effective_date == effective
        )
    ).first() is not None


def _update_asset(db, asset_id, effective, now, medians, auto_halt) -> str:
    from app.services.orders import lock_asset

    asset = lock_asset(db, asset_id)
    if _exists(db, asset_id, effective):
        return "already_done"
    channel = db.get(Channel, asset.channel_id)
    old = asset.reference_price

    if channel.data_quality_status == "unavailable" or is_stale(channel, now):
        reason = "channel data unavailable" if channel.data_quality_status == "unavailable" else (
            f"channel data older than {get_settings().stale_after_hours}h"
        )
        db.add(PriceUpdate(
            asset_id=asset_id, effective_date=effective, status="skipped", old_price=old,
            new_price=old, formula_version=FORMULA_VERSION, error=reason,
            inputs_json={"last_data_update_at": channel.last_data_update_at.isoformat()
                         if channel.last_data_update_at else None},
        ))
        db.flush()
        auto_halt(db, asset, "STALE_DATA" if "older" in reason else "DATA_UNAVAILABLE", reason)
        write_audit(db, "price.update_skipped", target_type="asset", target_id=asset_id,
                    metadata={"effective_date": effective.isoformat(), "reason": reason, "price": old})
        return "skipped"

    last = db.execute(
        select(PriceUpdate)
        .where(PriceUpdate.asset_id == asset_id, PriceUpdate.flow_window_end.is_not(None))
        .order_by(PriceUpdate.effective_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    window_start = last.flow_window_end if last else asset.created_at
    window_end = now

    from app.services.popularity import score_on  # local import avoids a cycle

    feats = compute_features(db, channel, effective)
    pop_now = asset.popularity_score
    pop_prev = score_on(db, asset_id, effective - timedelta(days=7))
    pop_median = _popularity_median(db)
    fund, fund_info = fundamental_return(feats, *medians, pop_now=pop_now, pop_prev=pop_prev)
    flow, flow_info = flow_return(db, asset_id, window_start, window_end,
                                  market_cap=asset.reference_price * asset.shares_outstanding)
    sent, sent_info = sentiment_return(db, asset_id, now, pop_now, pop_median)
    raw, daily = combine(fund, flow, sent)
    new = next_price(old, daily)

    db.add(PriceUpdate(
        asset_id=asset_id, effective_date=effective, status="applied", old_price=old, new_price=new,
        formula_version=FORMULA_VERSION,
        fundamental_return=Decimal(str(round(fund, 8))), flow_return=Decimal(str(round(flow, 8))),
        sentiment_return=Decimal(str(round(sent, 8))), raw_return=raw.quantize(Decimal("1e-8")),
        daily_return=daily.quantize(Decimal("1e-8")),
        flow_window_start=window_start, flow_window_end=window_end,
        inputs_json={
            "features": asdict(feats), "market_medians": list(medians),
            "fundamental": fund_info, "flow": flow_info, "sentiment": sent_info,
            "base_price": old, "data_source": channel.data_source,
            "popularity": {"now": pop_now, "7d_ago": pop_prev, "market_median": pop_median},
        },
    ))
    db.flush()  # UNIQUE(asset_id, effective_date) guards against double application here
    asset.previous_price = old
    asset.reference_price = new
    asset.current_price = new
    asset.price_updated_at = now
    asset.version += 1
    bars.record_reference_bar(db, asset_id, new, datetime.combine(effective, time(), UTC))
    write_audit(db, "price.update_applied", target_type="asset", target_id=asset_id, metadata={
        "effective_date": effective.isoformat(), "old_price": old, "new_price": new,
        "daily_return": str(daily), "fundamental": fund, "flow": flow, "sentiment": sent,
        "formula_version": FORMULA_VERSION,
    })
    return "applied"
