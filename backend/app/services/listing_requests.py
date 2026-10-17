"""User-submitted listing requests. They only create a *pending* channel; an administrator
must approve every listing."""

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Channel, User
from app.security import hit_rate_limit, utcnow
from app.services.listing import CHANNEL_ID_RE, register_channel
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
            "limit_reached": used >= s.listing_requests_per_day}


ID_IN_TEXT = re.compile(r"(UC[A-Za-z0-9_-]{22})")
HANDLE_IN_TEXT = re.compile(r"(?:^|youtube\.com/)(@[A-Za-z0-9._-]{3,30})(?:[/?#]|$)")


def parse_channel_ref(text: str) -> tuple[str, str]:
    """Returns ('id', 'UC...') or ('handle', '@name')."""
    text = text.strip()
    m = ID_IN_TEXT.search(text)
    if m and CHANNEL_ID_RE.match(m.group(1)):
        return "id", m.group(1)
    m = HANDLE_IN_TEXT.search(text)
    if m:
        return "handle", m.group(1)
    raise AppError(422, "INVALID_CHANNEL", "채널 ID(UC…), 채널 URL 또는 @핸들을 입력하세요")


def submit(db: Session, user: User, channel_ref: str, note: str | None,
           api: YouTubeDataAPIProvider | None, request_id: str | None) -> Channel:
    if user.status != "active":
        raise AppError(403, "ACCOUNT_RESTRICTED", "Restricted accounts cannot request listings")
    st = window_status(db)
    if not st["open"]:
        raise AppError(409, "LISTING_REQUESTS_CLOSED", "상장 요청은 매일 오후 7시부터 가능합니다",
                       {"opens_at": st["opens_at"].isoformat()})
    if st["limit_reached"]:
        raise AppError(409, "LISTING_REQUEST_LIMIT_REACHED", "오늘의 상장 요청 최대한도에 도달했습니다",
                       {"limit": st["limit"]})
    kind, value = parse_channel_ref(channel_ref)
    if kind == "handle":
        if api is None:
            raise AppError(422, "HANDLE_NEEDS_API", "@핸들 조회에는 YouTube API 설정이 필요합니다. 채널 ID(UC…)를 입력하세요")
        try:
            resolved = api.resolve_handle(value)
        except ProviderError:
            raise AppError(503, "YOUTUBE_UNAVAILABLE", "YouTube API에 연결할 수 없습니다. 잠시 후 다시 시도하세요")
        if not resolved:
            raise AppError(404, "CHANNEL_NOT_FOUND", "해당 핸들의 채널을 찾을 수 없습니다")
        value = resolved
    existing = db.execute(select(Channel).where(Channel.youtube_channel_id == value)).scalar_one_or_none()
    if existing is not None:
        raise AppError(409, "ALREADY_REQUESTED", f"이미 등록된 채널입니다 (상태: {existing.listing_status})",
                       {"listing_status": existing.listing_status})
    hit_rate_limit(f"listing_req:user:{user.id}", *get_settings().rl_listing_requests_per_user)
    title = value
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
    # Remote approval: every administrator gets a notification that links to the approval screen.
    for admin_id in db.execute(select(User.id).where(User.role == "admin", User.status == "active")).scalars():
        notify(db, admin_id, "listing_request", "새 상장 요청", f"{user.username}: {title}", "/admin?tab=listing")
    return ch


def mine(db: Session, user: User) -> list[dict]:
    rows = db.execute(select(Channel).where(Channel.requested_by == user.id).order_by(Channel.id.desc())).scalars()
    return [{"id": c.id, "youtube_channel_id": c.youtube_channel_id, "title": c.title,
             "listing_status": c.listing_status, "created_at": c.created_at, "note": c.request_note} for c in rows]
