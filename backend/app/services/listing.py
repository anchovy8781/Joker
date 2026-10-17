"""Channel registration/listing, trading halts and the IPO market maker.

The market maker is a normal account with role='system'. At listing it receives the
asset's full share inventory (shares, not credits). It quotes real resting limit orders
around the reference price, sells only shares it holds and buys only with cash it earned
from those sales — so it never creates credits and never produces fills without a
genuine counterparty.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Account, Channel, ChannelMetric, MarketAsset, Position, User
from app.security import utcnow
from app.services import bars, market_hours
from app.services.audit import write_audit
from app.services.fees import bps_down, bps_up, buy_reservation
from app.services.notify import notify, notify_holders
from app.services.orders import OrderRequest, cancel_all_for_asset, lock_asset, place_order
from app.services.youtube import ChannelSnapshot, DemoProvider

MM_USERNAME = "market_maker"
AUTO_PREFIX = "AUTO:"
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
DEMO_ID_RE = re.compile(r"^DEMO[A-Za-z0-9_-]{2,40}$")


def get_market_maker(db: Session) -> User:
    mm = db.execute(select(User).where(User.normalized_username == MM_USERNAME)).scalar_one_or_none()
    if mm is None:
        mm = User(username=MM_USERNAME, normalized_username=MM_USERNAME, role="system",
                  password_hash=None, verification_status="verified")
        db.add(mm)
        db.flush()
        db.add(Account(user_id=mm.id, cash_balance=0, reserved_cash=0))
        db.flush()
    return mm


def register_channel(
    db: Session, *, youtube_channel_id: str, title: str, data_source: str,
    category: str | None, actor_id: int | None, request_id: str | None = None,
    requested_by: int | None = None, request_note: str | None = None,
) -> Channel:
    if data_source == "youtube_api" and not CHANNEL_ID_RE.match(youtube_channel_id):
        raise AppError(422, "INVALID_CHANNEL_ID", "YouTube channel IDs look like UC + 22 characters")
    if data_source == "demo" and not DEMO_ID_RE.match(youtube_channel_id):
        raise AppError(422, "INVALID_CHANNEL_ID", "Demo channel IDs must start with DEMO")
    if db.execute(select(Channel.id).where(Channel.youtube_channel_id == youtube_channel_id)).first():
        raise AppError(409, "CHANNEL_EXISTS", "This channel is already registered")
    ch = Channel(youtube_channel_id=youtube_channel_id, title=title[:200], data_source=data_source,
                 category=category, listing_status="pending", requested_by=requested_by,
                 request_note=request_note)
    db.add(ch)
    db.flush()
    write_audit(db, "channel.register", actor_id=actor_id, target_type="channel", target_id=ch.id,
                request_id=request_id, metadata={"youtube_channel_id": youtube_channel_id,
                                                 "data_source": data_source,
                                                 "requested_by": requested_by})
    return ch


def store_snapshot(db: Session, channel: Channel, snap: ChannelSnapshot, observed_at: datetime) -> None:
    from sqlalchemy import text

    values = {"view_count": snap.view_count, "video_count": snap.video_count}
    if snap.subscriber_count is not None:
        values["subscriber_count"] = snap.subscriber_count
    for metric_type, value in values.items():
        db.execute(
            text(
                "INSERT INTO channel_metrics (channel_id, metric_type, metric_value, observed_at, "
                "observed_date, source, source_timestamp, data_quality_status) "
                "VALUES (:c, :t, :v, :o, :d, :s, :st, :q) "
                "ON CONFLICT (channel_id, metric_type, source, observed_date) DO UPDATE SET "
                "metric_value = EXCLUDED.metric_value, observed_at = EXCLUDED.observed_at, "
                "source_timestamp = EXCLUDED.source_timestamp"
            ),
            {"c": channel.id, "t": metric_type, "v": value, "o": observed_at,
             "d": observed_at.date(), "s": channel.data_source, "st": snap.source_timestamp,
             "q": "ok"},
        )


def store_engagement(db: Session, channel: Channel, eng, observed_at: datetime) -> None:
    """Totals over recent uploads (likes may be hidden by the creator -> not stored)."""
    from sqlalchemy import text

    values = {"recent_view_count": eng.views}
    if eng.likes is not None:
        values["recent_like_count"] = eng.likes
    if eng.comments is not None:
        values["recent_comment_count"] = eng.comments
    for metric_type, value in values.items():
        db.execute(
            text(
                "INSERT INTO channel_metrics (channel_id, metric_type, metric_value, observed_at, "
                "observed_date, source, source_timestamp, data_quality_status) "
                "VALUES (:c, :t, :v, :o, :d, :s, NULL, 'ok') "
                "ON CONFLICT (channel_id, metric_type, source, observed_date) DO UPDATE SET "
                "metric_value = EXCLUDED.metric_value, observed_at = EXCLUDED.observed_at"
            ),
            {"c": channel.id, "t": metric_type, "v": value, "o": observed_at, "d": observed_at.date(),
             "s": channel.data_source},
        )


def backfill_demo_history(db: Session, channel: Channel, days: int = 35, now: datetime | None = None) -> None:
    """Demo channels get synthetic history so the engine can run immediately (labelled demo)."""
    assert channel.data_source == "demo"
    now = now or utcnow()
    demo = DemoProvider()
    for back in range(days, -1, -1):
        day = (now - timedelta(days=back)).date()
        snap = demo.snapshot_for(channel.youtube_channel_id, channel.title, day)
        at = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
        store_snapshot(db, channel, snap, at)
        store_engagement(db, channel, demo.engagement_for(channel.youtube_channel_id, day), at)
    channel.last_data_update_at = now
    channel.data_quality_status = "ok"


def approve_listing(
    db: Session, *, channel_id: int, symbol: str, initial_price: int, shares_outstanding: int,
    actor_id: int, request_id: str | None = None, celebrity_new_channel: bool = False,
) -> MarketAsset:
    """Every listing is an explicit administrator decision (recorded with the admin's id)."""
    s = get_settings()
    _require_admin(db, actor_id)
    db.flush()
    channel = db.execute(select(Channel).where(Channel.id == channel_id).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if channel is None:
        raise AppError(404, "CHANNEL_NOT_FOUND", "Channel not found")
    if channel.listing_status != "pending":
        raise AppError(409, "INVALID_LISTING_STATE", f"Channel is {channel.listing_status}")
    symbol = symbol.upper()
    if not SYMBOL_RE.match(symbol):
        raise AppError(422, "INVALID_SYMBOL", "Symbol must be 2-12 uppercase letters/digits")
    if db.execute(select(MarketAsset.id).where(MarketAsset.symbol == symbol)).first():
        raise AppError(409, "SYMBOL_TAKEN", "Symbol already in use")
    if not (s.min_price <= initial_price <= 10_000_000):
        raise AppError(422, "INVALID_PRICE", f"initial_price must be >= {s.min_price}")
    if not (1_000 <= shares_outstanding <= 1_000_000_000):
        raise AppError(422, "INVALID_SHARES", "shares_outstanding must be 1,000..1,000,000,000")
    if channel.data_source == "demo":
        backfill_demo_history(db, channel)
    now = utcnow()
    asset = MarketAsset(channel_id=channel.id, symbol=symbol, reference_price=initial_price,
                        previous_price=initial_price, current_price=initial_price,
                        shares_outstanding=shares_outstanding, trading_status="active",
                        price_updated_at=now)
    db.add(asset)
    channel.listing_status = "listed"
    channel.listed_at = now
    db.flush()
    if channel.data_source == "youtube_api" and channel.last_data_update_at is None:
        # Never trade on data we have not verified yet.
        asset.trading_status = "halted"
        asset.halt_reason = f"{AUTO_PREFIX}AWAITING_DATA — waiting for first YouTube API collection"
    mm = get_market_maker(db)
    db.add(Position(user_id=mm.id, asset_id=asset.id, quantity=shares_outstanding, reserved_quantity=0,
                    average_cost=Decimal(0), realized_pnl=Decimal(0)))
    bars.record_reference_bar(db, asset.id, initial_price, now)
    db.flush()
    write_audit(db, "asset.list", actor_id=actor_id, target_type="asset", target_id=asset.id,
                request_id=request_id, metadata={"symbol": symbol, "initial_price": initial_price,
                                                 "ipo_shares_to_market_maker": shares_outstanding,
                                                 "channel_id": channel.id,
                                                 "celebrity_new_channel": celebrity_new_channel})
    if celebrity_new_channel:
        asset.celebrity_until = now + timedelta(days=s.celebrity_surcharge_days)
    if channel.requested_by:
        notify(db, channel.requested_by, "listing_approved", f"상장 승인: {symbol}",
               f"신청하신 채널 '{channel.title}'이(가) {symbol}(으)로 상장되었습니다.", f"/assets/{asset.id}")
    if asset.trading_status == "active":
        requote_market_maker(db, asset.id)
    return asset


def _require_admin(db: Session, actor_id: int | None) -> None:
    actor = db.get(User, actor_id) if actor_id is not None else None
    if actor is None or actor.role != "admin":
        raise AppError(403, "FORBIDDEN", "Listing decisions require an administrator")


def reject_listing(db: Session, channel_id: int, reason: str, actor_id: int, request_id: str | None) -> Channel:
    _require_admin(db, actor_id)
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise AppError(404, "CHANNEL_NOT_FOUND", "Channel not found")
    if channel.listing_status != "pending":
        raise AppError(409, "INVALID_LISTING_STATE", f"Channel is {channel.listing_status}")
    channel.listing_status = "rejected"
    from app.services.listing_requests import refund_fee  # (circular at import time)
    refunded = refund_fee(db, channel, actor_id)
    if channel.requested_by:
        extra = f" 신청 비용 {refunded:,} 크레딧은 환불되었습니다." if refunded else ""
        notify(db, channel.requested_by, "listing_rejected", "상장 신청 거절",
               f"'{channel.title}' 상장 신청이 거절되었습니다. 사유: {reason}.{extra}", "/listings")
    write_audit(db, "channel.reject", actor_id=actor_id, target_type="channel", target_id=channel_id,
                request_id=request_id, metadata={"reason": reason, "fee_refunded": refunded})
    return channel


def halt(db: Session, asset_id: int, reason: str, actor_id: int | None, request_id: str | None = None) -> MarketAsset:
    asset = lock_asset(db, asset_id)
    before = asset.trading_status
    asset.trading_status = "halted"
    asset.halt_reason = reason[:200]
    asset.version += 1
    write_audit(db, "asset.halt", actor_id=actor_id, target_type="asset", target_id=asset_id,
                request_id=request_id, metadata={"reason": reason, "before": before})
    notify_holders(db, asset_id, "asset_halted", f"{asset.symbol} 거래 정지",
                   f"보유 종목 {asset.symbol}의 거래가 정지되었습니다. 사유: {reason}", f"/assets/{asset_id}")
    return asset


def resume(db: Session, asset_id: int, reason: str, actor_id: int | None, request_id: str | None = None) -> MarketAsset:
    asset = lock_asset(db, asset_id)
    channel = db.get(Channel, asset.channel_id)
    from app.services.pricing import is_stale

    if channel.data_quality_status == "unavailable" or is_stale(channel, utcnow()):
        raise AppError(409, "DATA_NOT_FRESH", "Cannot resume: channel data is stale or unavailable")
    asset.trading_status = "active"
    asset.halt_reason = None
    asset.version += 1
    write_audit(db, "asset.resume", actor_id=actor_id, target_type="asset", target_id=asset_id,
                request_id=request_id, metadata={"reason": reason})
    requote_market_maker(db, asset_id)
    return asset


def auto_halt(db: Session, asset: MarketAsset, code: str, detail: str) -> None:
    """System halt for data problems. Never overrides a manual (operator) halt reason."""
    if asset.trading_status == "halted" and not (asset.halt_reason or "").startswith(AUTO_PREFIX):
        return
    if asset.trading_status == "halted" and (asset.halt_reason or "").startswith(f"{AUTO_PREFIX}{code}"):
        return
    asset.trading_status = "halted"
    asset.halt_reason = f"{AUTO_PREFIX}{code} — {detail}"[:200]
    asset.version += 1
    write_audit(db, "asset.auto_halt", target_type="asset", target_id=asset.id,
                metadata={"code": code, "detail": detail})
    notify_holders(db, asset.id, "asset_halted", f"{asset.symbol} 자동 거래 정지",
                   f"채널 데이터 문제로 거래가 정지되었습니다: {detail}", f"/assets/{asset.id}")


def auto_resume_if_safe(db: Session, asset: MarketAsset) -> bool:
    if asset.trading_status != "halted" or not (asset.halt_reason or "").startswith(AUTO_PREFIX):
        return False
    asset.trading_status = "active"
    asset.halt_reason = None
    asset.version += 1
    write_audit(db, "asset.auto_resume", target_type="asset", target_id=asset.id,
                metadata={"reason": "channel data is fresh again"})
    requote_market_maker(db, asset.id)
    return True


def requote_market_maker(db: Session, asset_id: int) -> dict:
    """Replace the market maker's quotes on one asset around the current reference price."""
    s = get_settings()
    mm = get_market_maker(db)
    asset = lock_asset(db, asset_id)
    canceled = cancel_all_for_asset(db, asset_id, mm.id, "MM_REQUOTE")
    placed = 0
    if asset.trading_status != "active" or not market_hours.is_open(utcnow()):
        return {"canceled": canceled, "placed": 0}
    ref = asset.reference_price
    pos = db.get(Position, (mm.id, asset_id))
    inventory = (pos.quantity - pos.reserved_quantity) if pos else 0
    account = db.get(Account, mm.id)
    n_assets = max(1, db.query(MarketAsset).count())
    cash_budget = (account.cash_balance - account.reserved_cash) // n_assets // 2
    for bps in s.mm_spread_bps:
        level_qty = max(1, inventory * s.mm_level_fraction_bps // 10_000) if inventory > 0 else 0
        if level_qty > 0:
            price = bps_up(ref, bps)
            r = place_order(db, mm, OrderRequest(asset_id, "sell", "limit", level_qty, price,
                                                 f"mm:{uuid.uuid4().hex}"))
            if r.order.status in ("open", "partially_filled", "filled"):
                inventory -= level_qty
                placed += 1
        bid = bps_down(ref, bps)
        if bid >= s.min_price and cash_budget > 0:
            unit = buy_reservation(1, bid)
            qty = min(cash_budget // len(s.mm_spread_bps) // unit, s.max_order_quantity)
            if qty > 0:
                place_order(db, mm, OrderRequest(asset_id, "buy", "limit", qty, bid,
                                                 f"mm:{uuid.uuid4().hex}"))
                placed += 1
    return {"canceled": canceled, "placed": placed}
