"""인기도 (popularity) score from 1 to 50, recomputed with every data collection.

Components (each normalised to 0..1):
    reach       25%  log-scaled subscribers (1천 → 0, 1천만 → 1)
    momentum    20%  7-day view growth, ranked against the other listed channels
    engagement  20%  like rate on recent uploads (likes / views; 6% or more → 1)
    likes       15%  log-scaled likes on recent uploads (100 → 0, 100만 → 1)
    buzz        10%  YouTube trending (1.0) or a new upload in the last 48h (0.6)
    market      10%  24h trading value, ranked against the other listed assets
When a channel hides its like counts, the engagement and likes weights move to the
other components, so missing data neither rewards nor punishes the channel.

score = 1 + round(49 × weighted sum)
"""

import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Channel, ChannelMetric, MarketAsset, MarketEvent, PopularityHistory, Trade
from app.services.pricing import compute_features

KST = ZoneInfo("Asia/Seoul")
WEIGHTS = {"reach": 0.25, "momentum": 0.20, "engagement": 0.20, "likes": 0.15, "buzz": 0.10, "market": 0.10}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _latest(db: Session, channel: Channel, metric: str) -> int | None:
    return db.execute(
        select(ChannelMetric.metric_value).where(
            ChannelMetric.channel_id == channel.id, ChannelMetric.metric_type == metric,
            ChannelMetric.source == channel.data_source,
        ).order_by(ChannelMetric.observed_date.desc()).limit(1)
    ).scalar()


def _rank(values: dict[int, float | None]) -> dict[int, float]:
    """Percentile rank in 0..1 (ties share the average); None -> 0.5 (neutral)."""
    known = {k: v for k, v in values.items() if v is not None}
    out = {k: 0.5 for k in values}
    if len(known) < 2:
        return out
    ordered = sorted(known.values())
    n = len(ordered)
    for k, v in known.items():
        lo = ordered.index(v)
        hi = n - 1 - ordered[::-1].index(v)
        out[k] = ((lo + hi) / 2) / (n - 1)
    return out


def score_components(reach, momentum, engagement, likes, buzz, market) -> tuple[int, dict]:
    parts = {"reach": reach, "momentum": momentum, "engagement": engagement, "likes": likes,
             "buzz": buzz, "market": market}
    weights = dict(WEIGHTS)
    missing = [k for k, v in parts.items() if v is None]
    for k in missing:  # redistribute missing weight proportionally
        weights.pop(k)
    total_w = sum(weights.values())
    weighted = sum(weights[k] / total_w * parts[k] for k in weights)
    score = 1 + round(49 * _clamp01(weighted))
    return score, {"components": {k: (round(v, 4) if v is not None else None) for k, v in parts.items()},
                   "weights": {k: round(w / total_w, 4) for k, w in weights.items()}, "weighted": round(weighted, 4)}


def recompute_all(db: Session, now: datetime) -> dict[str, int]:
    rows = db.execute(
        select(MarketAsset, Channel).join(Channel, Channel.id == MarketAsset.channel_id)
        .where(Channel.listing_status == "listed")
    ).all()
    if not rows:
        return {}
    day = now.date()
    since = now - timedelta(hours=24)
    turnover = dict(db.execute(
        select(Trade.asset_id, func.sum(Trade.quantity * Trade.execution_price))
        .where(Trade.executed_at >= since).group_by(Trade.asset_id)
    ).all())
    view_growth: dict[int, float | None] = {}
    raw: dict[int, dict] = {}
    for asset, ch in rows:
        f = compute_features(db, ch, day)
        view_growth[asset.id] = f.view_g7
        subs = _latest(db, ch, "subscriber_count")
        views_total = _latest(db, ch, "view_count")
        recent_views = _latest(db, ch, "recent_view_count")
        recent_likes = _latest(db, ch, "recent_like_count")
        reach_base = subs if subs else (views_total / 200 if views_total else None)
        reach = _clamp01((math.log10(reach_base) - 3) / 4) if reach_base else None
        engagement = likes = None
        if recent_likes is not None and recent_views:
            engagement = _clamp01((recent_likes / recent_views) / 0.06)
            likes = _clamp01((math.log10(recent_likes + 1) - 2) / 4)
        trending = db.execute(select(MarketEvent.id).where(
            MarketEvent.asset_id == asset.id, MarketEvent.event_type == "trending",
            MarketEvent.observed_at <= now, MarketEvent.expires_at > now)).first()
        buzz = 1.0 if trending else (0.6 if asset.new_video_until and asset.new_video_until > now else 0.0)
        raw[asset.id] = {"reach": reach, "engagement": engagement, "likes": likes, "buzz": buzz}
    momentum = _rank(view_growth)
    market_rank = _rank({a.id: float(turnover.get(a.id, 0)) for a, _ in rows})
    if not turnover:
        market_rank = {k: 0.0 for k in market_rank}
    out = {}
    local_day = now.astimezone(KST).date()
    for asset, _ in rows:
        r = raw[asset.id]
        score, detail = score_components(r["reach"], momentum[asset.id], r["engagement"], r["likes"],
                                         r["buzz"], market_rank[asset.id])
        asset.popularity_score = score
        asset.popularity_updated_at = now
        db.execute(text(
            "INSERT INTO popularity_history (asset_id, score_date, score, components_json) "
            "VALUES (:a, :d, :s, CAST(:c AS jsonb)) ON CONFLICT (asset_id, score_date) "
            "DO UPDATE SET score = EXCLUDED.score, components_json = EXCLUDED.components_json"),
            {"a": asset.id, "d": local_day, "s": score, "c": json.dumps(detail)})
        out[asset.symbol] = score
    db.flush()
    return out


def score_on(db: Session, asset_id: int, day) -> int | None:
    return db.execute(select(PopularityHistory.score).where(
        PopularityHistory.asset_id == asset_id, PopularityHistory.score_date <= day)
        .order_by(PopularityHistory.score_date.desc()).limit(1)).scalar()
