"""Live market view: intraday channel samples and the 2-second asset snapshot.

YouTube statistics cannot be polled every 2 seconds (free API quota), so they are sampled
every few minutes (`collect_live`, 1 quota unit per 50 channels) and the live view derives
rates (views/hour, subscribers/hour) from those samples and *estimates* the current counts
by extrapolating — clearly flagged as estimates. In-game data (price, trades, volume,
order book) is read directly and is exact.
"""

import math
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Channel, ChannelSample, MarketAsset, Order, Trade
from app.security import utcnow
from app.services.youtube import DemoProvider, ProviderError, YouTubeDataAPIProvider, _unit

RESTING = ("open", "partially_filled")


def _demo_sample(demo: DemoProvider, ch: Channel, now: datetime) -> tuple[int, int, int]:
    """Smooth intraday path between two daily demo snapshots plus a small deterministic wobble."""
    day = now.date()
    a = demo.snapshot_for(ch.youtube_channel_id, ch.title, day)
    b = demo.snapshot_for(ch.youtube_channel_id, ch.title, day + timedelta(days=1))
    frac = (now - datetime(day.year, day.month, day.day, tzinfo=UTC)).total_seconds() / 86400
    # Views arrive faster at some hours: a gentle wave that is 0 at both ends of the day, so the
    # path is continuous across midnight and never runs backwards (counts only grow).
    shape = frac + 0.03 * math.sin(2 * math.pi * frac)
    views = int(a.view_count + (b.view_count - a.view_count) * shape)
    subs = int(a.subscriber_count + (b.subscriber_count - a.subscriber_count) * shape)
    return subs, max(views, a.view_count), a.video_count


def collect_live(db: Session, api: YouTubeDataAPIProvider | None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    s = get_settings()
    channels = db.execute(select(Channel).where(Channel.listing_status.in_(("pending", "listed")))).scalars().all()
    demo = DemoProvider()
    out = {"demo": 0, "api": 0, "errors": []}
    for ch in channels:
        if ch.data_source == "demo":
            subs, views, videos = _demo_sample(demo, ch, now)
            db.add(ChannelSample(channel_id=ch.id, observed_at=now, subscriber_count=subs,
                                 view_count=views, video_count=videos, source="demo"))
            out["demo"] += 1
    api_channels = [c for c in channels if c.data_source == "youtube_api"]
    if api_channels and api is not None:
        try:
            fetched = api.fetch_channels([c.youtube_channel_id for c in api_channels])
            for ch in api_channels:
                snap = fetched.snapshots.get(ch.youtube_channel_id)
                if snap is None:
                    continue
                db.add(ChannelSample(channel_id=ch.id, observed_at=now, subscriber_count=snap.subscriber_count,
                                     view_count=snap.view_count, video_count=snap.video_count,
                                     source="youtube_api"))
                ch.title, ch.thumbnail_url = snap.title, snap.thumbnail_url or ch.thumbnail_url
                ch.handle = snap.handle or ch.handle
                out["api"] += 1
        except ProviderError as exc:
            out["errors"].append(str(exc))
    db.execute(delete(ChannelSample).where(ChannelSample.observed_at < now - timedelta(days=s.live_samples_keep_days)))
    from app.services.bars import prune_minute_bars

    prune_minute_bars(db)
    db.flush()
    return out


def _rate_per_hour(samples: list[ChannelSample], attr: str, window: timedelta) -> float | None:
    """Growth per hour over the most recent `window` of samples (needs two distinct values in time)."""
    pts = [(x.observed_at, getattr(x, attr)) for x in samples if getattr(x, attr) is not None]
    if len(pts) < 2:
        return None
    end_t, end_v = pts[-1]
    start = next(((t, v) for t, v in pts if t >= end_t - window), pts[0])
    if start[0] >= end_t:
        start = pts[-2]
    hours = (end_t - start[0]).total_seconds() / 3600
    return (end_v - start[1]) / hours if hours > 0 else None


_cache: dict[int, tuple[float, dict]] = {}


def _whales(db: Session, asset_id: int, now: datetime) -> dict:
    """세력 동향: what the whale bots have been doing on this asset in the last 30 minutes."""
    from app.services import bots

    flow = bots.whale_net_flow(db, asset_id, now - timedelta(minutes=30))
    state = "매집" if flow["net"] > 0 else "분산" if flow["net"] < 0 else "관망"
    return {**flow, "state": state}


def snapshot(db: Session, asset: MarketAsset, channel: Channel, now: datetime | None = None) -> dict:
    """Everything the live panel needs in one call (cached for 1s per asset to absorb polling)."""
    hit = _cache.get(asset.id)
    if now is None and hit and time.monotonic() - hit[0] < 1.0:
        return hit[1]
    now = now or utcnow()

    def window(minutes: int) -> dict:
        since = now - timedelta(minutes=minutes)
        n, vol, val, buy_vol = db.execute(
            select(func.count(Trade.id), func.coalesce(func.sum(Trade.quantity), 0),
                   func.coalesce(func.sum(Trade.quantity * Trade.execution_price), 0),
                   func.coalesce(func.sum(Trade.quantity).filter(Trade.taker_side == "buy"), 0))
            .where(Trade.asset_id == asset.id, Trade.executed_at >= since)).one()
        return {"trades": int(n), "volume": int(vol), "value": int(val),
                "buy_ratio": round(int(buy_vol) / int(vol), 4) if vol else None}

    ticks = db.execute(
        select(Trade.executed_at, Trade.execution_price, Trade.quantity, Trade.taker_side)
        .where(Trade.asset_id == asset.id, Trade.executed_at >= now - timedelta(minutes=30))
        .order_by(Trade.executed_at.desc()).limit(300)).all()
    best_bid = db.execute(select(func.max(Order.limit_price)).where(
        Order.asset_id == asset.id, Order.side == "buy", Order.status.in_(RESTING))).scalar()
    best_ask = db.execute(select(func.min(Order.limit_price)).where(
        Order.asset_id == asset.id, Order.side == "sell", Order.status.in_(RESTING))).scalar()

    samples = db.execute(select(ChannelSample).where(
        ChannelSample.channel_id == channel.id, ChannelSample.observed_at >= now - timedelta(hours=24))
        .order_by(ChannelSample.observed_at)).scalars().all()
    yt: dict = {"source": channel.data_source, "samples": [
        {"t": x.observed_at, "views": x.view_count, "subs": x.subscriber_count} for x in samples]}
    if samples:
        last = samples[-1]
        vph = _rate_per_hour(samples, "view_count", timedelta(hours=1))
        sph = _rate_per_hour(samples, "subscriber_count", timedelta(hours=6))
        age_h = (now - last.observed_at).total_seconds() / 3600
        yt.update({
            "observed_at": last.observed_at, "views": last.view_count, "subscribers": last.subscriber_count,
            "videos": last.video_count, "views_per_hour": round(vph, 1) if vph is not None else None,
            "subs_per_hour": round(sph, 2) if sph is not None else None,
            # Extrapolated between samples (never below the last observed value).
            "est_views_now": int(last.view_count + max(vph or 0, 0) * age_h),
            "est_subs_now": (int(last.subscriber_count + max(sph or 0, 0) * age_h)
                             if last.subscriber_count is not None else None),
        })
    change = asset.current_price - asset.previous_price
    data = {
        "server_time": now,
        "asset": {"id": asset.id, "symbol": asset.symbol, "price": asset.current_price,
                  "previous_price": asset.previous_price, "change": change,
                  "change_pct": round(change / asset.previous_price * 100, 2) if asset.previous_price else 0.0,
                  "trading_status": asset.trading_status, "popularity_score": asset.popularity_score,
                  "last_trade_at": asset.last_trade_at},
        "game": {"m1": window(1), "m5": window(5), "h1": window(60), "h24": window(24 * 60),
                 "best_bid": best_bid, "best_ask": best_ask,
                 "spread_bps": (round((best_ask - best_bid) / best_ask * 10_000) if best_bid and best_ask else None)},
        "ticks": [{"t": t, "price": p, "qty": q, "side": side} for t, p, q, side in reversed(ticks)],
        "youtube": yt,
        "whales": _whales(db, asset.id, now),
        "intraday": {"adj_bps": asset.intraday_adj_bps, "detail": asset.intraday_detail,
                     "fair_price": max(get_settings().min_price,
                                       asset.reference_price * (10_000 + asset.intraday_adj_bps) // 10_000),
                     "reference_price": asset.reference_price, "updated_at": asset.intraday_updated_at},
    }
    _cache[asset.id] = (time.monotonic(), data)
    return data
