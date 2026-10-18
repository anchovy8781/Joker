"""Turn whatever a person typed (channel ID, channel URL, @handle or a bare handle) into a
YouTube channel ID, and suggest listing terms for an administrator."""

import html
import json
import re
import time
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


_PAGE_IMAGE = re.compile(r'<meta property="og:image" content="([^"]{1,500})"')
_PAGE_SUBS = [re.compile(r'"subscriberCountText":\{[^{}]*?"simpleText":"([^"]{1,40})"'),
              re.compile(r'"content":"(구독자 [^"]{1,30})"'),
              re.compile(r'"content":"([0-9.,]+[KMB만천억]?\s*subscribers?)"')]
# YouTube shows a cookie-consent interstitial to some regions without these.
_CONSENT = {"CONSENT": "YES+cb", "SOCS": "CAI"}
_HEADERS = {"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
PAGE_BUDGET_SECONDS = 8.0
PAGE_MAX_BYTES = 4_000_000
_TRANSPORT: httpx.BaseTransport | None = None  # tests swap in httpx.MockTransport


class _Slow(Exception):
    pass


def _get_page(url: str, params: dict | None = None) -> tuple[int, str]:
    """GET a YouTube page with a hard wall-clock budget and size cap (httpx's timeout is per
    read, so a slow trickle could otherwise hold a request — and the user — for minutes)."""
    deadline = time.monotonic() + PAGE_BUDGET_SECONDS
    with httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=True, cookies=_CONSENT,
                      headers=_HEADERS, transport=_TRANSPORT) as c:
        with c.stream("GET", url, params=params) as r:
            chunks, size = [], 0
            for chunk in r.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > PAGE_MAX_BYTES or time.monotonic() > deadline:
                    raise _Slow()
            return r.status_code, b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")


def fetch_public_page(path: str) -> dict | None:
    """Public channel page (no API key): channel ID, title, avatar, subscriber text."""
    try:
        status, page = _get_page(f"https://www.youtube.com/{quote(path, safe='@/')}")
    except (httpx.HTTPError, _Slow):
        raise AppError(503, "YOUTUBE_UNAVAILABLE", "YouTube 응답이 늦어요. 잠시 후 다시 시도하세요")
    if status == 404:
        return None
    if status != 200:
        raise AppError(503, "YOUTUBE_UNAVAILABLE", "YouTube에 연결할 수 없습니다. 잠시 후 다시 시도하세요")
    cid = next((m.group(1) for rx in _PAGE_ID if (m := rx.search(page))), None)
    if cid is None:
        return None
    t = _PAGE_TITLE.search(page)
    img = _PAGE_IMAGE.search(page)
    subs = next((m.group(1) for rx in _PAGE_SUBS if (m := rx.search(page))), None)
    return {"id": cid, "title": html.unescape(t.group(1)) if t else None,
            "thumbnail_url": html.unescape(img.group(1)) if img else None, "subscriber_text": subs}


def lookup_public_page(handle: str) -> tuple[str, str | None] | None:
    """Fallback without an API key: read the channel ID from the public channel page."""
    page = fetch_public_page(handle)
    return (page["id"], page["title"]) if page else None


def resolve(text: str, api: YouTubeDataAPIProvider | None) -> tuple[str, str | None]:
    """Channel ID and (when known) title for a channel reference. Raises AppError."""
    kind, value = parse_channel_ref(text)
    if kind == "id":
        return value, None
    if api is not None:
        try:
            cid = api.resolve_handle(value)
            if cid is not None:
                return cid, None
        except ProviderError:
            pass  # key restricted / quota exhausted / API down: use the public page instead
    found = lookup_public_page(value)
    if found is None:
        raise AppError(404, "CHANNEL_NOT_FOUND", "해당 핸들의 채널을 찾을 수 없습니다. 철자를 확인해 주세요")
    return found


def preview(db: Session, text: str, api: YouTubeDataAPIProvider | None) -> dict:
    """What the user is about to request: profile, counts and whether it is already listed."""
    cid, title = resolve(text, api)
    kind, ref = parse_channel_ref(text)
    out = {"youtube_channel_id": cid, "title": title or cid, "handle": ref if kind == "handle" else None,
           "thumbnail_url": None, "subscriber_count": None, "view_count": None, "video_count": None,
           "subscriber_text": None, "hidden_subscriber_count": False, "source": None}
    snap = None
    if api is not None:
        try:
            snap = api.fetch_channels([cid]).snapshots.get(cid)
        except ProviderError:
            snap = None
    if snap is not None:
        out.update(title=snap.title, handle=snap.handle or out["handle"], thumbnail_url=snap.thumbnail_url,
                   subscriber_count=snap.subscriber_count, view_count=snap.view_count, video_count=snap.video_count,
                   hidden_subscriber_count=snap.hidden_subscriber_count, source="youtube_api")
    else:
        page = fetch_public_page(out["handle"] or f"channel/{cid}")
        if page:
            out.update(title=page["title"] or out["title"], thumbnail_url=page["thumbnail_url"],
                       subscriber_text=page["subscriber_text"], source="public_page")
    existing = db.execute(select(Channel).where(Channel.youtube_channel_id == cid)).scalar_one_or_none()
    out["listing_status"] = existing.listing_status if existing else None
    if existing is not None:
        asset = db.execute(select(MarketAsset.id).where(MarketAsset.channel_id == existing.id)).scalar()
        out["asset_id"] = asset
    return out


def _nice_price(raw: float) -> int:
    """Round to 2 significant digits within the allowed listing range."""
    p = max(1_000, min(100_000, int(raw)))
    digits = len(str(p)) - 2
    return int(round(p, -digits)) if digits > 0 else p


_CHO = ["G", "KK", "N", "D", "TT", "R", "M", "B", "PP", "S", "SS", "", "J", "JJ", "CH", "K", "T", "P", "H"]
_JUNG = ["A", "AE", "YA", "YAE", "EO", "E", "YEO", "YE", "O", "WA", "WAE", "OE", "YO", "U", "WO", "WE", "WI", "YU",
         "EU", "UI", "I"]
_JONG = ["", "K", "K", "K", "N", "N", "N", "T", "L", "K", "M", "L", "L", "L", "P", "L", "M", "P", "P", "T", "T", "NG",
         "T", "T", "K", "T", "P", "T"]


def romanize(text: str) -> str:
    """Korean (Hangul syllables) to Latin letters, Revised Romanization style without the
    finer sound-change rules: 침착맨 → CHIMCHAKMAEN, 보따 → BOTTA. Other letters and digits
    pass through; everything else is dropped."""
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out.append(_CHO[code // 588] + _JUNG[(code % 588) // 28] + _JONG[code % 28])
        elif ch.isascii() and ch.isalnum():
            out.append(ch.upper())
    return "".join(out)


def symbol_base(channel: Channel) -> str:
    """Readable ticker stem: the handle's Latin letters, else the romanized title, else
    derived from the channel id."""
    base = re.sub(r"[^A-Z0-9]", "", (channel.handle or "").upper())
    if len(base) < 2:
        base = romanize(re.sub(r"^\[DEMO\]\s*", "", channel.title))
    if len(base) < 2:
        base = "YT" + re.sub(r"[^A-Z0-9]", "", channel.youtube_channel_id.upper())[-6:]
    return base[:10]


def unique_symbol(db: Session, base: str, exclude_asset_id: int | None = None) -> str:
    symbol, n = base, 1
    while True:
        taken = db.execute(select(MarketAsset.id).where(MarketAsset.symbol == symbol)).scalar()
        if taken is None or taken == exclude_asset_id:
            return symbol
        n += 1
        symbol = f"{base[:12 - len(str(n))]}{n}"


def suggest_listing(db: Session, channel: Channel) -> dict:
    """Default listing terms so an administrator can approve with one tap."""
    subs = db.execute(select(ChannelMetric.metric_value).where(
        ChannelMetric.channel_id == channel.id, ChannelMetric.metric_type == "subscriber_count",
    ).order_by(ChannelMetric.observed_at.desc()).limit(1)).scalar()
    price = _nice_price(subs / 100) if subs else 10_000
    symbol = unique_symbol(db, symbol_base(channel))
    return {"symbol": symbol, "initial_price": price, "shares_outstanding": 1_000_000,
            "subscriber_count": subs}


_INITIAL_DATA = re.compile(r"(?:var ytInitialData|window\[\"ytInitialData\"\])\s*=\s*(\{.+?\});\s*</script>", re.S)


def _walk(node, key: str):
    """Yield every value stored under `key` anywhere inside a JSON tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from _walk(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, key)


def _text(v) -> str | None:
    if not isinstance(v, dict):
        return None
    if "simpleText" in v:
        return v["simpleText"]
    runs = v.get("runs")
    return "".join(r.get("text", "") for r in runs) if isinstance(runs, list) else None


def parse_search_page(page: str, limit: int = 8) -> list[dict]:
    m = _INITIAL_DATA.search(page)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    out, seen = [], set()
    for r in _walk(data, "channelRenderer"):
        cid = r.get("channelId") if isinstance(r, dict) else None
        if not cid or not CHANNEL_ID_RE.match(cid) or cid in seen:
            continue
        seen.add(cid)
        thumbs = ((r.get("thumbnail") or {}).get("thumbnails") or [])
        thumb = thumbs[-1]["url"] if thumbs else None
        if thumb and thumb.startswith("//"):
            thumb = "https:" + thumb
        base = ((r.get("navigationEndpoint") or {}).get("browseEndpoint") or {}).get("canonicalBaseUrl") or ""
        # YouTube puts the handle in subscriberCountText and the count in videoCountText on new layouts.
        texts = [_text(r.get("subscriberCountText")), _text(r.get("videoCountText"))]
        handle = base[1:] if base.startswith("/@") else next((t for t in texts if t and t.startswith("@")), None)
        subs = next((t for t in texts if t and not t.startswith("@")), None)
        out.append({"youtube_channel_id": cid, "title": _text(r.get("title")) or cid, "handle": handle,
                    "thumbnail_url": thumb, "subscriber_text": subs})
        if len(out) >= limit:
            break
    return out


def search_channels(query: str, api: YouTubeDataAPIProvider | None, limit: int = 8) -> list[dict]:
    """Channels matching a name. A handle, URL or channel ID resolves to that one channel.
    Name search reads YouTube's public results page (free); the Data API search costs
    100 quota units, so it is only the fallback when the page cannot be read."""
    q = query.strip()
    try:
        kind, value = parse_channel_ref(q)
    except AppError:
        kind = None
    if kind == "id" or (kind == "handle" and ("youtube.com" in q or q.startswith("@"))):
        cid, title = resolve(q, api)
        return [{"youtube_channel_id": cid, "title": title or cid, "handle": value if kind == "handle" else None,
                 "thumbnail_url": None, "subscriber_text": None}]
    try:
        status, page = _get_page("https://www.youtube.com/results",
                                 {"search_query": q, "sp": "EgIQAg=="})  # channels only
        found = parse_search_page(page, limit) if status == 200 else []
    except (httpx.HTTPError, _Slow):
        found = []
    if found or api is None:
        if not found and api is None:
            # Last resort without a key: treat the text as a handle.
            try:
                cid, title = resolve(q, None)
                return [{"youtube_channel_id": cid, "title": title or cid, "handle": "@" + q.lstrip("@"),
                         "thumbnail_url": None, "subscriber_text": None}]
            except AppError:
                return []
        return found
    try:
        return api.search_channels(q, limit)
    except ProviderError:
        return []
