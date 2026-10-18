"""Channel search by name, runtime (one-touch) settings, second candles, investor bots."""

import json
import random
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models import MarketAsset, Position, Trade, User
from app.services import bots, channel_lookup, runtime_settings

CID1, CID2 = "UC" + "a" * 22, "UC" + "b" * 22


@pytest.fixture(autouse=True)
def _restore_settings():
    yield
    runtime_settings.reset_for_tests()


def _results_page(*channels) -> str:
    items = [{"channelRenderer": {
        "channelId": cid, "title": {"simpleText": title},
        "thumbnail": {"thumbnails": [{"url": "//yt3.ggpht.com/small"}, {"url": "//yt3.ggpht.com/big"}]},
        "navigationEndpoint": {"browseEndpoint": {"canonicalBaseUrl": handle}},
        "videoCountText": {"simpleText": subs}}} for cid, title, handle, subs in channels]
    data = {"contents": {"sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": items}}]}}}
    return f"<html><script>var ytInitialData = {json.dumps(data, ensure_ascii=False)};</script></html>"


def test_search_by_name_fills_channel_ids(make_user, monkeypatch):
    page = _results_page((CID1, "침착맨", "/@chimchakman", "구독자 300만명"), (CID2, "침착맨 클립", "/@clip", "구독자 10만명"))
    seen = {}

    def fake_get(url, params=None, **kw):
        seen.update(params or {})
        return httpx.Response(200, text=page)
    monkeypatch.setattr(channel_lookup.httpx, "get", fake_get)
    r = make_user().get("/api/channels/search", params={"q": "침착맨"})
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [x["youtube_channel_id"] for x in rows] == [CID1, CID2]
    assert rows[0] == {"youtube_channel_id": CID1, "title": "침착맨", "handle": "@chimchakman",
                       "thumbnail_url": "https://yt3.ggpht.com/big", "subscriber_text": "구독자 300만명"}
    assert seen["search_query"] == "침착맨"


def test_search_with_an_id_or_handle_returns_that_channel(make_user, monkeypatch):
    rows = make_user().get("/api/channels/search", params={"q": f"https://youtube.com/channel/{CID1}"}).json()
    assert rows[0]["youtube_channel_id"] == CID1
    monkeypatch.setattr(channel_lookup, "fetch_public_page", lambda p: {"id": CID2, "title": "H", "thumbnail_url": None,
                                                                        "subscriber_text": None})
    rows = make_user().get("/api/channels/search", params={"q": "@handleonly"}).json()
    assert rows[0]["youtube_channel_id"] == CID2 and rows[0]["handle"] == "@handleonly"


def test_unreadable_search_page_returns_empty_list():
    assert channel_lookup.parse_search_page("<html>nothing</html>") == []


def test_admin_switches_settings_with_one_tap(make_admin, make_user, db):
    admin = make_admin()
    rows = {r["key"]: r for r in admin.get("/api/admin/settings").json()}
    assert rows["bots_enabled"]["kind"] == "bool" and rows["listing_fee"]["choices"]
    assert admin.post("/api/admin/settings/listing_window_enabled", json={"value": False}).status_code == 200
    assert get_settings().listing_window_enabled is False
    assert make_user().get("/api/listing-requests/status").json()["open"] is True
    assert admin.post("/api/admin/settings/listing_fee", json={"value": 50_000}).json()["value"] == 50_000
    assert get_settings().listing_fee == 50_000
    bad = admin.post("/api/admin/settings/listing_fee", json={"value": 12_345})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_SETTING"
    assert admin.post("/api/admin/settings/database_url", json={"value": True}).status_code == 404
    assert make_user().post("/api/admin/settings/bots_enabled", json={"value": False}).status_code == 403
    # Overrides survive a re-read from the database (another process / restart)
    runtime_settings.reset_for_tests()
    runtime_settings.apply(db)
    assert get_settings().listing_fee == 50_000


def test_second_candles_come_from_trades(make_user, asset, db):
    u = make_user()
    for _ in range(3):
        assert u.order(asset, "buy", 2).status_code == 201
    bars = u.get(f"/api/assets/{asset}/history", params={"interval": "5s", "limit": 60}).json()
    assert len(bars) == 60
    assert sum(b["volume"] for b in bars) == 6
    stamps = [datetime.fromisoformat(b["timestamp"]) for b in bars]
    assert all((b - a) == timedelta(seconds=5) for a, b in zip(stamps, stamps[1:]))
    assert bars[-1]["close_price"] == db.get(MarketAsset, asset).current_price
    assert u.get(f"/api/assets/{asset}/history", params={"interval": "3m"}).status_code == 200
    assert u.get(f"/api/assets/{asset}/history", params={"interval": "30m"}).status_code == 200


def test_investor_archetype_bots_follow_their_style(db, asset):
    names = {p.username: p.kind for p in bots.PERSONAS}
    assert names["버핏형_가치투자"] == "buffett" and names["멍거형_집중투자"] == "munger"
    assert names["골드만형_투자은행"] == "goldman"
    a = db.get(MarketAsset, asset)
    rng = random.Random(1)
    buffett = bots.BY_NAME["버핏형_가치투자"]
    cheap = {"fair": a.current_price * 1.05, "ret_10m": 0.0, "whale": (None, 0), "top_quality": {a.id}, "yt_bps": 0}
    fair = {**cheap, "fair": a.current_price}
    assert bots._decide(buffett, rng, a, cheap, held=0)[0] == "buy"   # good channel on sale
    assert bots._decide(buffett, rng, a, fair, held=0) is None         # no bargain, no trade
    munger = bots.BY_NAME["멍거형_집중투자"]
    assert bots._decide(munger, rng, a, {**fair, "top_quality": set()}, held=0) is None  # not a top channel
    goldman = bots.BY_NAME["골드만형_투자은행"]
    assert bots._decide(goldman, rng, a, {**cheap}, held=0)[0] == "buy"  # arbitrage toward fair value
    # Long-term investors never turn a sell into a buy when they hold nothing.
    assert bots._order(db, db.get(User, bots.ensure_bots(db)[-3].id), a, "sell", 1_000_000, rng, "buffett") is False
