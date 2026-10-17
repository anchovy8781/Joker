"""User-submitted listing requests. They only create a *pending* channel; an administrator
must approve every listing."""

import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Account, Channel, LedgerEntry, User
from app.security import hit_rate_limit, utcnow
from app.services import channel_lookup
from app.services.ledger import lock_account, post_entry
from app.services.listing import register_channel
from app.services.notify import notify
from app.services.youtube import ProviderError, YouTubeDataAPIProvider

KST = ZoneInfo("Asia/Seoul")


def window_status(db: Session, now: datetime | None = None) -> dict:
    """Requests open every day at 19:00 KST (until midnight); at most 25 per day in total."""
    s = get_settings()
    now = now or utcnow()
    local = now.astimezone(KST)
    day_start = datetime.combine(local.date(), time(), KST)
    opens_at = day_start + timedelta(hours=s.listing_request_open_hour_kst)
    used = db.execute(select(func.count(Channel.id)).where(
        Channel.requested_by.is_not(None), Channel.created_at >= day_start)).scalar_one()
    is_open = local >= opens_at
    return {"open": is_open, "opens_at": opens_at if not is_open else None,
            "next_opens_at": opens_at if not is_open else opens_at + timedelta(days=1),
            "closes_at": day_start + timedelta(days=1), "limit": s.listing_requests_per_day,
            "used": used, "remaining": max(0, s.listing_requests_per_day - used),
            "limit_reached": used >= s.listing_requests_per_day, "listing_fee": s.listing_fee}


def _fee_paid(db: Session, channel_id: int) -> LedgerEntry | None:
    return db.execute(select(LedgerEntry).where(
        LedgerEntry.reference_type == "channel", LedgerEntry.reference_id == str(channel_id),
        LedgerEntry.entry_type == "listing_fee")).scalar_one_or_none()


def refund_fee(db: Session, channel: Channel, actor_id: int | None) -> int:
    """Return the listing fee of a rejected request (once). Returns the refunded amount."""
    paid = _fee_paid(db, channel.id)
    if paid is None:
        return 0
    already = db.execute(select(LedgerEntry.id).where(
        LedgerEntry.reference_type == "channel", LedgerEntry.reference_id == str(channel.id),
        LedgerEntry.entry_type == "listing_fee_refund")).first()
    if already:
        return 0
    account = lock_account(db, paid.user_id)
    post_entry(db, account, entry_type="listing_fee_refund", amount=-paid.amount, transaction_id=uuid.uuid4(),
               reference_type="channel", reference_id=channel.id, actor_id=actor_id,
               memo="Listing request rejected: fee refunded")
    return -paid.amount


def submit(db: Session, user: User, channel_ref: str, note: str | None,
           api: YouTubeDataAPIProvider | None, request_id: str | None) -> Channel:
    s = get_settings()
    if user.status != "active":
        raise AppError(403, "ACCOUNT_RESTRICTED", "Restricted accounts cannot request listings")
    st = window_status(db)
    if not st["open"]:
        raise AppError(409, "LISTING_REQUESTS_CLOSED", "상장 요청은 매일 오후 7시부터 가능합니다",
                       {"opens_at": st["opens_at"].isoformat()})
    if st["limit_reached"]:
        raise AppError(409, "LISTING_REQUEST_LIMIT_REACHED", "오늘의 상장 요청 최대한도에 도달했습니다",
                       {"limit": st["limit"]})
    acct = db.get(Account, user.id)
    if s.listing_fee > 0 and (acct is None or acct.cash_balance - acct.reserved_cash < s.listing_fee):
        raise AppError(409, "INSUFFICIENT_FUNDS",
                       f"상장 신청 비용 {s.listing_fee:,} 크레딧이 필요합니다 (미션으로 모을 수 있어요)",
                       {"listing_fee": s.listing_fee})
    value, title = channel_lookup.resolve(channel_ref, api)
    existing = db.execute(select(Channel).where(Channel.youtube_channel_id == value)).scalar_one_or_none()
    if existing is not None:
        raise AppError(409, "ALREADY_REQUESTED", f"이미 등록된 채널입니다 (상태: {existing.listing_status})",
                       {"listing_status": existing.listing_status})
    hit_rate_limit(f"listing_req:user:{user.id}", *get_settings().rl_listing_requests_per_user)
    title = title or value
    if api is not None:  # verify the channel exists and take its official title
        try:
            fetched = api.fetch_channels([value])
        except ProviderError:
            fetched = None
        if fetched is not None:
            snap = fetched.snapshots.get(value)
            if snap is None:
                raise AppError(404, "CHANNEL_NOT_FOUND", "YouTube에서 채널을 찾을 수 없습니다")
            title = snap.title
    # Serialise the global daily cap, then re-count inside the lock.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('yex_listing_requests'))"))
    if window_status(db)["limit_reached"]:
        raise AppError(409, "LISTING_REQUEST_LIMIT_REACHED", "오늘의 상장 요청 최대한도에 도달했습니다")
    ch = register_channel(db, youtube_channel_id=value, title=title, data_source="youtube_api",
                          category=None, actor_id=user.id, request_id=request_id,
                          requested_by=user.id, request_note=(note or None))
    kind, ref = channel_lookup.parse_channel_ref(channel_ref)
    if kind == "handle":
        ch.handle = ref
    if s.listing_fee > 0:
        post_entry(db, lock_account(db, user.id), entry_type="listing_fee", amount=-s.listing_fee,
                   transaction_id=uuid.uuid4(), reference_type="channel", reference_id=ch.id,
                   actor_id=user.id, memo=f"Listing request fee: {title}")
    # Remote approval: every administrator gets a notification that links to the approval screen.
    for admin_id in db.execute(select(User.id).where(User.role == "admin", User.status == "active")).scalars():
        notify(db, admin_id, "listing_request", "새 상장 요청", f"{user.username}: {title}", "/admin?tab=listing")
    return ch


def mine(db: Session, user: User) -> list[dict]:
    rows = db.execute(select(Channel).where(Channel.requested_by == user.id).order_by(Channel.id.desc())).scalars()
    return [{"id": c.id, "youtube_channel_id": c.youtube_channel_id, "title": c.title,
             "listing_status": c.listing_status, "created_at": c.created_at, "note": c.request_note,
             "fee_paid": -fee.amount if (fee := _fee_paid(db, c.id)) else 0} for c in rows]
