"""Turn whatever a person typed (channel ID, channel URL, @handle or a bare handle) into a
YouTube channel ID, and suggest listing terms for an administrator."""

import re
from urllib.parse import quote, unquote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Channel, ChannelMetric, MarketAsset
from app.services.listing import CHANNEL_ID_RE
from app.services.youtube import ProviderError, YouTubeDataAPIProvider

ID_IN_TEXT = re.compile(r"(UC[A-Za-z0-9_-]{22})")
# Handles may use letters of any script (e.g. Korean), digits, '.', '_' and '-'.
HANDLE_CHARS = r"[\w.\-]{3,30}"
HANDLE_IN_URL = re.compile(rf"youtube\.com/(@{HANDLE_CHARS})(?:[/?#]|$)")
BARE_HANDLE = re.compile(rf"^@?({HANDLE_CHARS})$")

_PAGE_ID = [re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})"'),
            re.compile(r'"externalId":"(UC[A-Za-z0-9_-]{22})"'),
            re.compile(r'<meta itemprop="identifier" content="(UC[A-Za-z0-9_-]{22})"')]
_PAGE_TITLE = re.compile(r'<meta property="og:title" content="([^"]{1,200})"')


def parse_channel_ref(text: str) -> tuple[str, str]:
    """Returns ('id', 'UC...') or ('handle', '@name')."""
    text = unquote(text.strip())
    m = ID_IN_TEXT.search(text)
    if m and CHANNEL_ID_RE.match(m.group(1)):
        return "id", m.group(1)
    m = HANDLE_IN_URL.search(text)
    if m:
        return "handle", m.group(1)
    m = BARE_HANDLE.match(text)
    if m:
        return "handle", "@" + m.group(1)
    raise AppError(422, "INVALID_CHANNEL", "채널 @핸들, 채널 주소 또는 채널 ID(UC…)를 입력하세요")


def lookup_public_page(handle: str) -> tuple[str, str | None] | None:
    """Fallback without an API key: read the channel ID from the public channel page."""
    try:
        r = httpx.get(f"https://www.youtube.com/{quote(handle)}", timeout=10, follow_redirects=True,
                      headers={"Accept-Language": "ko,en;q=0.8", "User-Agent": "Mozilla/5.0"})
    except httpx.HTTPError:
        raise AppError(503, "YOUTUBE_UNAVAILABLE", "YouTube에 연결할 수 없습니다. 잠시 후 다시 시도하세요")
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise AppError(503, "YOUTUBE_UNAVAILABLE", "YouTube에 연결할 수 없습니다. 잠시 후 다시 시도하세요")
    for rx in _PAGE_ID:
        m = rx.search(r.text)
        if m:
            t = _PAGE_TITLE.search(r.text)
            return m.group(1), (t.group(1) if t else None)
    return None


def resolve(text: str, api: YouTubeDataAPIProvider | None) -> tuple[str, str | None]:
    """Channel ID and (when known) title for a channel reference. Raises AppError."""
    kind, value = parse_channel_ref(text)
    if kind == "id":
        return value, None
    if api is not None:
        try:
            cid = api.resolve_handle(value)
            if cid is None:
                raise AppError(404, "CHANNEL_NOT_FOUND", "해당 핸들의 채널을 찾을 수 없습니다")
            return cid, None
        except ProviderError:
            pass  # quota exhausted / API down: try the public page instead
    found = lookup_public_page(value)
    if found is None:
        raise AppError(404, "CHANNEL_NOT_FOUND", "해당 핸들의 채널을 찾을 수 없습니다")
    return found


def _nice_price(raw: float) -> int:
    """Round to 2 significant digits within the allowed listing range."""
    p = max(1_000, min(100_000, int(raw)))
    digits = len(str(p)) - 2
    return int(round(p, -digits)) if digits > 0 else p


def suggest_listing(db: Session, channel: Channel) -> dict:
    """Default listing terms so an administrator can approve with one tap."""
    subs = db.execute(select(ChannelMetric.metric_value).where(
        ChannelMetric.channel_id == channel.id, ChannelMetric.metric_type == "subscriber_count",
    ).order_by(ChannelMetric.observed_at.desc()).limit(1)).scalar()
    price = _nice_price(subs / 100) if subs else 10_000
    base = re.sub(r"[^A-Z0-9]", "", (channel.handle or "").upper())
    if len(base) < 2:
        base = re.sub(r"[^A-Z0-9]", "", channel.title.upper())
    if len(base) < 2:
        base = "YT" + re.sub(r"[^A-Z0-9]", "", channel.youtube_channel_id.upper())[-6:]
    base = base[:10]
    symbol, n = base, 1
    while db.execute(select(MarketAsset.id).where(MarketAsset.symbol == symbol)).first():
        n += 1
        symbol = f"{base[:12 - len(str(n))]}{n}"
    return {"symbol": symbol, "initial_price": price, "shares_outstanding": 1_000_000,
            "subscriber_count": subs}
