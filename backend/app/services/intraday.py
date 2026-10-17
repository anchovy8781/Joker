"""Intraday fair value: how far the market maker's quote centre moves off the daily reference.

The daily engine (pricing.py) sets the reference price once a day. Between those updates
the market maker re-centres its quotes every few minutes on

    fair = reference × (1 + adj),   adj = clamp(yt + subs + flow, ±max_bps)

  * yt   — current views/hour (last hour of live samples) vs the channel's 7-day average
           pace: 150 bps × ln(now / baseline), within ±250 bps
  * subs — subscribers/hour (last 6h) vs the 7-day pace: 80 bps × ln(ratio), within ±100 bps
  * flow — taker buy/sell imbalance of the last 30 minutes, scaled by how many trades it is
           based on (≥10 trades = full weight), within ±100 bps; the market maker's own
           trades as taker are ignored

Players still set the traded price: quotes only move where liquidity sits. Missing data
contributes 0 rather than a guess.
"""

import math
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Channel, ChannelMetric, ChannelSample, MarketAsset, Trade
from app.security import utcnow
from app.services.live import _rate_per_hour


def _clamp(v: float, lim: int) -> int:
    return int(max(-lim, min(lim, round(v))))


def _baseline_per_hour(db: Session, channel: Channel, metric: str, now: datetime) -> float | None:
    rows = db.execute(select(ChannelMetric.observed_at, ChannelMetric.metric_value).where(
        ChannelMetric.channel_id == channel.id, ChannelMetric.metric_type == metric,
        ChannelMetric.source == channel.data_source, ChannelMetric.observed_at >= now - timedelta(days=8),
    ).order_by(ChannelMetric.observed_at)).all()
    if len(rows) < 2:
        return None
    (t0, v0), (t1, v1) = rows[0], rows[-1]
    hours = (t1 - t0).total_seconds() / 3600
    return (v1 - v0) / hours if hours >= 24 else None


def _log_ratio(now: float | None, base: float | None) -> float | None:
    if now is None or base is None or base <= 0:
        return None
    if now <= 0:
        return -3.0  # stalled or shrinking while the channel normally grows
    return math.log(now / base)


def compute(db: Session, asset: MarketAsset, mm_user_id: int, now: datetime | None = None) -> dict:
    s = get_settings()
    now = now or utcnow()
    channel = db.get(Channel, asset.channel_id)
    samples = db.execute(select(ChannelSample).where(
        ChannelSample.channel_id == channel.id, ChannelSample.observed_at >= now - timedelta(hours=7))
        .order_by(ChannelSample.observed_at)).scalars().all()
    vph = _rate_per_hour(samples, "view_count", timedelta(hours=1))
    sph = _rate_per_hour(samples, "subscriber_count", timedelta(hours=6))
    vph_base = _baseline_per_hour(db, channel, "view_count", now)
    sph_base = _baseline_per_hour(db, channel, "subscriber_count", now)
    lv, ls = _log_ratio(vph, vph_base), _log_ratio(sph, sph_base)
    yt_bps = _clamp(150 * lv, 250) if lv is not None else 0
    subs_bps = _clamp(80 * ls, 100) if ls is not None else 0

    since = now - timedelta(minutes=30)
    buy, sell, n = db.execute(select(
        func.coalesce(func.sum(Trade.quantity).filter(Trade.taker_side == "buy"), 0),
        func.coalesce(func.sum(Trade.quantity).filter(Trade.taker_side == "sell"), 0),
        func.count(Trade.id),
    ).where(Trade.asset_id == asset.id, Trade.executed_at >= since,
            # ignore trades where the market maker was the aggressor
            ~(((Trade.taker_side == "buy") & (Trade.buyer_id == mm_user_id))
              | ((Trade.taker_side == "sell") & (Trade.seller_id == mm_user_id))))).one()
    buy, sell, n = int(buy), int(sell), int(n)
    imbalance = (buy - sell) / (buy + sell) if buy + sell else 0.0
    flow_bps = _clamp(100 * imbalance * min(1.0, n / 10), 100)

    total = _clamp(yt_bps + subs_bps + flow_bps, s.intraday_max_adj_bps)
    return {"adj_bps": total, "yt_bps": yt_bps, "subs_bps": subs_bps, "flow_bps": flow_bps,
            "views_per_hour": round(vph, 1) if vph is not None else None,
            "views_per_hour_7d": round(vph_base, 1) if vph_base is not None else None,
            "subs_per_hour": round(sph, 2) if sph is not None else None,
            "subs_per_hour_7d": round(sph_base, 2) if sph_base is not None else None,
            "flow_trades_30m": n, "flow_imbalance": round(imbalance, 3)}


def update(db: Session, asset: MarketAsset, mm_user_id: int, now: datetime | None = None) -> int:
    now = now or utcnow()
    detail = compute(db, asset, mm_user_id, now)
    asset.intraday_adj_bps = detail["adj_bps"]
    asset.intraday_detail = detail
    asset.intraday_updated_at = now
    return detail["adj_bps"]


def fair_price(asset: MarketAsset) -> int:
    return max(get_settings().min_price, asset.reference_price * (10_000 + asset.intraday_adj_bps) // 10_000)
