"""Channel data collection and verified market-event detection.

On provider errors the last verified data is kept as-is (with its original timestamp), so
staleness is visible and the price engine refuses to use it once it is too old.
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Channel, MarketAsset, MarketEvent
from app.security import utcnow
from app.services.audit import write_audit
from app.services.listing import auto_halt, auto_resume_if_safe, store_snapshot
from app.services.pricing import compute_features, is_stale
from app.services.youtube import DemoProvider, ProviderError, YouTubeDataAPIProvider

log = logging.getLogger("yex.collector")


def _add_event(db: Session, asset_id: int, event_type: str, score: float, source: str,
               observed: datetime, ttl_days: int, dedup: str, details: dict) -> bool:
    exists = db.execute(select(MarketEvent.id).where(MarketEvent.deduplication_key == dedup)).first()
    if exists:
        return False
    try:
        with db.begin_nested():
            db.add(MarketEvent(asset_id=asset_id, event_type=event_type,
                               event_score=Decimal(str(score)), source=source, observed_at=observed,
                               expires_at=observed + timedelta(days=ttl_days),
                               deduplication_key=dedup, details_json=details))
            db.flush()
    except IntegrityError:
        return False
    return True


def detect_events(db: Session, channel: Channel, asset: MarketAsset, day: date, now: datetime,
                  trending: bool) -> list[str]:
    """Only rule-based events computed from verified provider data. User posts or rumours
    are never turned into events."""
    created = []
    src = channel.data_source
    week = f"{day.isocalendar().year}W{day.isocalendar().week:02d}"
    if trending and _add_event(db, asset.id, "trending", 0.5, src, now, 3,
                               f"trending:{asset.id}:{day.isoformat()}", {"chart": "mostPopular"}):
        created.append("trending")
    f = compute_features(db, channel, day)
    if f.has_28d and f.sub_g7 is not None and f.sub_g28w is not None:
        if f.sub_g7 > 0.005 and f.sub_g7 > 2 * max(f.sub_g28w, 0):
            if _add_event(db, asset.id, "growth_acceleration", 0.3, src, now, 7,
                          f"accel:{asset.id}:{week}", {"sub_g7": f.sub_g7, "sub_g28w": f.sub_g28w}):
                created.append("growth_acceleration")
        if f.sub_g7 < -0.005:
            if _add_event(db, asset.id, "subscriber_loss", -0.3, src, now, 7,
                          f"subloss:{asset.id}:{week}", {"sub_g7": f.sub_g7}):
                created.append("subscriber_loss")
    if f.has_28d and f.view_g7 is not None and f.view_g28w and f.view_g7_prev is not None:
        # Sustained: both of the last two weeks gained views at < 50% of the 28-day weekly pace.
        if f.view_g7 < 0.5 * f.view_g28w and f.view_g7_prev < 0.5 * f.view_g28w:
            if _add_event(db, asset.id, "sustained_view_decline", -0.4, src, now, 7,
                          f"viewdecline:{asset.id}:{week}",
                          {"view_g7": f.view_g7, "view_g7_prev": f.view_g7_prev, "view_g28w": f.view_g28w}):
                created.append("sustained_view_decline")
    return created


def collect_all(db: Session, now: datetime | None = None,
                api: YouTubeDataAPIProvider | None = None) -> dict:
    now = now or utcnow()
    day = now.date()
    demo = DemoProvider()
    channels = db.execute(
        select(Channel).where(Channel.listing_status.in_(("pending", "listed"))).order_by(Channel.id)
    ).scalars().all()
    result = {"demo_updated": 0, "api_updated": 0, "api_missing": [], "errors": [], "events": 0}
    api_channels = [c for c in channels if c.data_source == "youtube_api"]

    for ch in channels:
        if ch.data_source != "demo":
            continue
        store_snapshot(db, ch, demo.snapshot_for(ch.youtube_channel_id, ch.title, day), now)
        ch.last_data_update_at = now
        ch.data_quality_status = "ok"
        result["demo_updated"] += 1

    trending_api: set[str] = set()
    if api_channels:
        if api is None:
            result["errors"].append("YEX_YOUTUBE_API_KEY not configured; real channels not updated")
        else:
            try:
                fetched = api.fetch_channels([c.youtube_channel_id for c in api_channels])
                for ch in api_channels:
                    snap = fetched.snapshots.get(ch.youtube_channel_id)
                    if snap is None:
                        ch.data_quality_status = "unavailable"
                        result["api_missing"].append(ch.youtube_channel_id)
                        continue
                    store_snapshot(db, ch, snap, now)
                    ch.title = snap.title
                    ch.thumbnail_url = snap.thumbnail_url
                    ch.handle = snap.handle
                    ch.hidden_subscriber_count = snap.hidden_subscriber_count
                    ch.last_data_update_at = now
                    ch.data_quality_status = "low_confidence" if snap.hidden_subscriber_count else "ok"
                    result["api_updated"] += 1
                for region in get_settings().youtube_trending_regions:
                    trending_api |= api.fetch_trending_channels(region)
            except ProviderError as exc:
                # Keep last verified data untouched; staleness will surface naturally.
                result["errors"].append(f"youtube_api: {exc}")
                log.warning("YouTube collection failed: %s", exc)

    # Freshness → halts/resumes, then events.
    for ch in channels:
        if ch.listing_status != "listed":
            continue
        asset = db.execute(select(MarketAsset).where(MarketAsset.channel_id == ch.id)).scalar_one()
        if ch.data_quality_status == "unavailable":
            auto_halt(db, asset, "DATA_UNAVAILABLE",
                      "channel not returned by YouTube API (deleted, terminated or restricted)")
            continue
        if is_stale(ch, now):
            auto_halt(db, asset, "STALE_DATA", f"no fresh data for {get_settings().stale_after_hours}h")
            continue
        auto_resume_if_safe(db, asset)
        if ch.last_data_update_at == now:
            trending = (demo.is_trending(ch.youtube_channel_id, day) if ch.data_source == "demo"
                        else ch.youtube_channel_id in trending_api)
            result["events"] += len(detect_events(db, ch, asset, day, now, trending))

    write_audit(db, "data.collect", metadata={k: v for k, v in result.items()})
    db.flush()
    return result
