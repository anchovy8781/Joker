"""Channel data providers.

* YouTubeDataAPIProvider — official YouTube Data API v3 (`channels.list`, `videos.list`
  chart=mostPopular). Uses only public fields the API returns; free daily quota, API key only.
* DemoProvider — deterministic synthetic numbers for channels explicitly registered as
  `data_source='demo'`. Every row it produces is stored with source='demo' and the UI labels it.

The two never mix: a channel's data_source decides which provider may write its metrics.
"""

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine

API_BASE = "https://www.googleapis.com/youtube/v3"


class ProviderError(Exception):
    pass


class QuotaExceeded(ProviderError):
    pass


@dataclass
class ChannelSnapshot:
    youtube_channel_id: str
    title: str
    thumbnail_url: str | None
    handle: str | None
    subscriber_count: int | None  # None when the channel hides it
    view_count: int
    video_count: int
    source_timestamp: datetime | None
    hidden_subscriber_count: bool


@dataclass
class FetchResult:
    snapshots: dict[str, ChannelSnapshot]
    missing: list[str]  # deleted/terminated/inaccessible channels


def consume_quota(provider: str, units: int, limit: int) -> None:
    """Atomically account API quota units; refuse calls that would exceed the daily limit.
    Uses its own connection so usage is counted even if the caller's transaction rolls back."""
    with get_engine().begin() as conn:
        used = conn.execute(
            text(
                "INSERT INTO api_quota_usage (provider, usage_date, units_used) VALUES (:p, :d, :u) "
                "ON CONFLICT (provider, usage_date) DO UPDATE "
                "SET units_used = api_quota_usage.units_used + EXCLUDED.units_used "
                "RETURNING units_used"
            ),
            {"p": provider, "d": datetime.now(UTC).date(), "u": units},
        ).scalar_one()
    if used > limit:
        raise QuotaExceeded(f"{provider} daily quota exhausted ({used}/{limit})")


class YouTubeDataAPIProvider:
    name = "youtube_api"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        if not api_key:
            raise ProviderError("YEX_YOUTUBE_API_KEY is not configured")
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=10)

    def _get(self, path: str, params: dict, units: int) -> dict:
        consume_quota(self.name, units, get_settings().youtube_daily_quota)
        try:
            r = self.client.get(f"{API_BASE}/{path}", params={**params, "key": self.api_key})
        except httpx.HTTPError as exc:
            raise ProviderError(f"network error: {type(exc).__name__}") from exc
        if r.status_code == 403 and "quota" in r.text.lower():
            raise QuotaExceeded("YouTube API reported quota exceeded")
        if r.status_code != 200:
            # Never echo the request URL: it contains the API key.
            raise ProviderError(f"YouTube API HTTP {r.status_code}")
        return r.json()

    def fetch_channels(self, channel_ids: list[str]) -> FetchResult:
        snapshots: dict[str, ChannelSnapshot] = {}
        for i in range(0, len(channel_ids), 50):  # API max 50 ids per call, 1 unit each
            batch = channel_ids[i : i + 50]
            data = self._get(
                "channels", {"part": "snippet,statistics", "id": ",".join(batch), "maxResults": 50}, 1
            )
            for item in data.get("items", []):
                st = item.get("statistics", {})
                sn = item.get("snippet", {})
                hidden = bool(st.get("hiddenSubscriberCount", False))
                thumbs = sn.get("thumbnails", {})
                thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url")
                snapshots[item["id"]] = ChannelSnapshot(
                    youtube_channel_id=item["id"],
                    title=sn.get("title", item["id"])[:200],
                    thumbnail_url=thumb,
                    handle=sn.get("customUrl"),
                    subscriber_count=None if hidden else int(st.get("subscriberCount", 0)),
                    view_count=int(st.get("viewCount", 0)),
                    video_count=int(st.get("videoCount", 0)),
                    source_timestamp=None,  # API does not timestamp statistics; we record fetch time
                    hidden_subscriber_count=hidden,
                )
        missing = [c for c in channel_ids if c not in snapshots]
        return FetchResult(snapshots, missing)

    def fetch_trending_channels(self, region: str) -> set[str]:
        data = self._get(
            "videos",
            {"part": "snippet", "chart": "mostPopular", "regionCode": region, "maxResults": 50},
            1,
        )
        ids = {(v.get("snippet") or {}).get("channelId") for v in data.get("items", [])}
        return {i for i in ids if i}


def _unit(seed: str) -> float:
    """Deterministic pseudo-random number in [0, 1)."""
    return int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16) / 16**12


class DemoProvider:
    """Synthetic, reproducible channel statistics. Clearly labelled as demo everywhere."""

    name = "demo"

    def snapshot_for(self, channel_id: str, title: str, day: date) -> ChannelSnapshot:
        base_subs = int(50_000 + _unit(channel_id + ":subs") * 5_000_000)
        base_views = base_subs * int(80 + _unit(channel_id + ":vpr") * 300)
        drift = (_unit(channel_id + ":drift") - 0.35) * 0.004  # per-day growth tendency
        epoch = date(2026, 1, 1)
        t = (day - epoch).days
        phase = _unit(channel_id + ":phase") * 6.28
        # Integrated growth = drift * t plus a slow wave so channels accelerate/decelerate.
        growth = drift * t + 0.03 * math.sin(t / 17 + phase)
        noise = (_unit(f"{channel_id}:{day.isoformat()}") - 0.5) * 0.002
        subs = max(100, int(base_subs * math.exp(growth + noise)))
        views = max(1000, int(base_views * math.exp(growth * 1.4 + noise + 0.001 * t)))
        return ChannelSnapshot(
            youtube_channel_id=channel_id,
            title=title,
            thumbnail_url=None,
            handle=None,
            subscriber_count=subs,
            view_count=views,
            video_count=int(100 + _unit(channel_id + ":videos") * 2000 + t // 3),
            source_timestamp=datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
            hidden_subscriber_count=False,
        )

    def is_trending(self, channel_id: str, day: date) -> bool:
        return _unit(f"{channel_id}:trend:{day.isoformat()}") < 0.05


def get_api_provider(client: httpx.Client | None = None) -> YouTubeDataAPIProvider | None:
    key = get_settings().youtube_api_key
    return YouTubeDataAPIProvider(key, client) if key else None
