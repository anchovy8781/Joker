"""User-submitted listing requests. They only create a *pending* channel; an administrator
must approve every listing."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Channel, User
from app.security import hit_rate_limit
from app.services.listing import CHANNEL_ID_RE, register_channel
from app.services.youtube import ProviderError, YouTubeDataAPIProvider

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
    return register_channel(db, youtube_channel_id=value, title=title, data_source="youtube_api",
                            category=None, actor_id=user.id, request_id=request_id,
                            requested_by=user.id, request_note=(note or None))


def mine(db: Session, user: User) -> list[dict]:
    rows = db.execute(select(Channel).where(Channel.requested_by == user.id).order_by(Channel.id.desc())).scalars()
    return [{"id": c.id, "youtube_channel_id": c.youtube_channel_id, "title": c.title,
             "listing_status": c.listing_status, "created_at": c.created_at, "note": c.request_note} for c in rows]
