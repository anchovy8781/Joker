"""Listing by handle + listing fee, admin grant, live snapshot and sparklines."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.cli import bootstrap
from app.config import get_settings
from app.errors import AppError
from app.models import Account, Channel, ChannelSample, LedgerEntry, MarketAsset, User
from app.security import utcnow
from app.services import channel_lookup, live, reconciliation
from app.services.listing import register_channel
from app.services.youtube import DemoProvider

CID = "UC" + "k" * 22


def _balance(u) -> int:
    return u.get("/api/account").json()["cash_balance"]


@pytest.mark.parametrize("text,expected", [
    ("@chimchakman", ("handle", "@chimchakman")),
    ("chimchakman", ("handle", "@chimchakman")),
    ("침착맨", ("handle", "@침착맨")),
    ("https://www.youtube.com/@%EC%B9%A8%EC%B0%A9%EB%A7%A8/videos", ("handle", "@침착맨")),
    (f"https://youtube.com/channel/{CID}", ("id", CID)),
])
def test_parse_channel_ref_accepts_bare_and_korean_handles(text, expected):
    assert channel_lookup.parse_channel_ref(text) == expected


def test_parse_channel_ref_rejects_sentences():
    with pytest.raises(AppError):
        channel_lookup.parse_channel_ref("not a channel")


def test_public_page_lookup_reads_channel_id(monkeypatch):
    page = (f'<html><meta property="og:title" content="침착맨">'
            f'<link rel="canonical" href="https://www.youtube.com/channel/{CID}"></html>')
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return httpx.Response(200, text=page)
    monkeypatch.setattr(channel_lookup.httpx, "get", fake_get)
    assert channel_lookup.lookup_public_page("@침착맨") == (CID, "침착맨")
    assert seen["url"] == "https://www.youtube.com/@%EC%B9%A8%EC%B0%A9%EB%A7%A8"  # keep the @
    monkeypatch.setattr(channel_lookup.httpx, "get", lambda url, **kw: httpx.Response(404))
    assert channel_lookup.lookup_public_page("@nobody") is None


def test_listing_fee_charged_and_refunded_on_rejection(make_user, make_admin, db, monkeypatch):
    fee = get_settings().listing_fee
    monkeypatch.setattr(channel_lookup, "lookup_public_page", lambda h: (CID, "Handle Channel"))
    u = make_user()
    start = _balance(u)
    assert start == get_settings().signup_grant == 1_000_000
    r = u.post("/api/listing-requests", json={"channel": "handlechannel"})
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Handle Channel"
    assert _balance(u) == start - fee
    assert u.get("/api/listing-requests").json()[0]["fee_paid"] == fee
    ch = db.execute(select(Channel).where(Channel.youtube_channel_id == CID)).scalar_one()
    assert ch.handle == "@handlechannel"

    admin = make_admin()
    rej = admin.post(f"/api/admin/channels/{ch.id}/reject", json={"reason": "not eligible"})
    assert rej.status_code == 200, rej.text
    assert _balance(u) == start  # refunded in full
    # A second rejection attempt cannot refund twice.
    assert admin.post(f"/api/admin/channels/{ch.id}/reject", json={"reason": "again"}).status_code == 409
    refunds = db.execute(select(func.count(LedgerEntry.id)).where(
        LedgerEntry.entry_type == "listing_fee_refund")).scalar_one()
    assert refunds == 1
    assert reconciliation.reconcile(db).status == "ok"


def test_listing_needs_enough_credits(make_user, db, monkeypatch):
    monkeypatch.setattr(channel_lookup, "lookup_public_page", lambda h: (CID, "x"))
    u = make_user()
    acct = db.get(Account, u.id)
    db.refresh(acct)
    monkeypatch.setattr(get_settings(), "listing_fee", acct.cash_balance + 1)
    r = u.post("/api/listing-requests", json={"channel": "@enoughcheck"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_FUNDS"
    assert db.execute(select(Channel).where(Channel.youtube_channel_id == CID)).first() is None


def test_admin_approves_with_suggested_terms(make_admin, db):
    ch = register_channel(db, youtube_channel_id="DEMO_suggest", title="[DEMO] Suggest", data_source="demo",
                          category=None, actor_id=None)
    ch.handle = "@sugg.est"
    db.commit()
    admin = make_admin()
    sug = admin.get(f"/api/admin/channels/{ch.id}/suggestion").json()
    assert sug["symbol"] == "SUGGEST" and sug["shares_outstanding"] == 1_000_000
    ok = admin.post(f"/api/admin/channels/{ch.id}/approve", json={"reason": "one tap approval"})
    assert ok.status_code == 201, ok.text
    assert ok.json()["symbol"] == "SUGGEST" and 1_000 <= ok.json()["initial_price"] <= 100_000


def test_admin_registers_channel_by_handle(make_admin, db, monkeypatch):
    monkeypatch.setattr(channel_lookup, "lookup_public_page", lambda h: (CID, "Found Title"))
    admin = make_admin()
    r = admin.post("/api/admin/channels", json={"youtube_channel_id": "@foundit", "data_source": "youtube_api"})
    assert r.status_code == 201, r.text
    assert r.json()["youtube_channel_id"] == CID and r.json()["title"] == "Found Title"


def test_bootstrap_pays_admin_grant_once(db, monkeypatch):
    env = {"YEX_BOOTSTRAP_ADMIN_USERNAME": "grantboss", "YEX_BOOTSTRAP_ADMIN_PASSWORD": "a-long-password-1"}
    bootstrap(env)
    bootstrap(env)
    admin = db.execute(select(User).where(User.username == "grantboss")).scalar_one()
    acct = db.get(Account, admin.id)
    db.refresh(acct)
    assert acct.cash_balance == get_settings().admin_grant == 100_000_000
    assert reconciliation.reconcile(db).status == "ok"


def test_live_snapshot_reports_game_activity_and_youtube_rates(make_user, asset, db):
    u = make_user()
    assert u.order(asset, "buy", 3).status_code == 201
    ch = db.get(Channel, db.get(MarketAsset, asset).channel_id)
    now = utcnow()
    for minutes in (60, 30, 0):
        live.collect_live(db, None, now - timedelta(minutes=minutes))
    db.commit()
    samples = db.execute(select(ChannelSample).where(ChannelSample.channel_id == ch.id)).scalars().all()
    assert len(samples) == 3 and samples[-1].view_count >= samples[0].view_count
    live._cache.clear()
    data = u.get(f"/api/assets/{asset}/live").json()
    assert data["game"]["m1"]["trades"] >= 1 and data["game"]["m1"]["buy_ratio"] == 1.0
    assert data["ticks"][-1]["qty"] >= 1
    yt = data["youtube"]
    assert yt["source"] == "demo" and len(yt["samples"]) == 3
    assert yt["views_per_hour"] is not None and yt["views_per_hour"] >= 0
    assert yt["est_views_now"] >= yt["views"]


def test_demo_intraday_path_stays_between_daily_snapshots(db):
    demo = DemoProvider()
    ch = Channel(youtube_channel_id="DEMO_path", title="p", data_source="demo")
    now = utcnow()
    subs, views, _ = live._demo_sample(demo, ch, now)
    today = demo.snapshot_for(ch.youtube_channel_id, ch.title, now.date())
    assert views >= today.view_count


def test_asset_list_includes_sparkline(make_user, asset, db):
    from app.services import bars

    u = make_user()
    row = next(a for a in u.get("/api/assets").json() if a["id"] == asset)
    assert row["sparkline"] == []  # too little history: no misleading line
    now = utcnow()
    for h, price in ((5, 9_800), (3, 10_100), (1, 10_300)):
        bars.record_trade_bar(db, asset, price, 1, now - timedelta(hours=h))
    db.commit()
    row = next(a for a in u.get("/api/assets").json() if a["id"] == asset)
    assert row["sparkline"][:3] == [9_800, 10_100, 10_300]
    assert row["sparkline"][-1] == row["current_price"]
