"""Chat, notifications, missions, rankings, approval-only listings and surcharge fees."""

import threading
import uuid
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.errors import AppError
from app.models import Account, Channel, LedgerEntry, MarketAsset, Order, Trade, User, UserMission
from app.security import utcnow
from app.services import collector
from app.services.listing import approve_listing, register_channel
from app.services.reconciliation import reconcile
from app.services.youtube import YouTubeDataAPIProvider
from tests.conftest import admin_id, give_shares, list_demo_asset


def acct(db, uid):
    db.expire_all()
    return db.get(Account, uid)


# ---------------------------------------------------------------- surcharge fees
def test_celebrity_listing_charges_surcharge_fee(make_user, db):
    aid = list_demo_asset(db, symbol="DCELEB", celebrity=True, with_mm_quotes=False)
    seller, buyer = make_user(), make_user()
    asset = db.get(MarketAsset, aid)
    assert asset.celebrity_until > utcnow() + timedelta(days=29)
    detail = buyer.get(f"/api/assets/{aid}").json()
    assert detail["fee_bps"] == 100 and detail["fee_surcharge_reason"] == "celebrity_new_channel"

    give_shares(db, seller.id, aid, 10)  # bought @10,000 with 1% fee -> avg cost 10,100
    seller.order(aid, "sell", 5, price=10_500)  # profit -> fee charged
    r = buyer.order(aid, "buy", 5, price=10_500).json()
    t = db.get(Trade, r["fills"][0]["id"])
    assert r["order"]["fee_bps"] == 100
    assert t.buyer_fee == 525 and t.seller_fee == 525 and not t.seller_fee_waived  # 1% of 52,500


def test_selling_surcharged_asset_at_a_loss_has_no_sell_fee(make_user, db):
    aid = list_demo_asset(db, symbol="DCELEB", celebrity=True, with_mm_quotes=False)
    seller, buyer = make_user(), make_user()
    give_shares(db, seller.id, aid, 10)  # avg cost 10,100
    cash_before = acct(db, seller.id).cash_balance
    seller.order(aid, "sell", 4, price=9_900)  # below average cost -> loss
    buyer.order(aid, "buy", 4, price=9_900)
    t = db.execute(select(Trade).where(Trade.seller_id == seller.id)).scalar_one()
    assert t.seller_fee == 0 and t.seller_fee_waived is True
    assert t.buyer_fee == 396  # buyer still pays 1%
    assert acct(db, seller.id).cash_balance == cash_before + 4 * 9_900
    mine = seller.get("/api/trades").json()
    assert mine[0]["fee"] == 0 and mine[0]["fee_waived"] is True
    assert reconcile(db).status == "ok"  # no fee ledger row for the waived fee


def test_normal_asset_keeps_base_fee_even_on_loss(make_user, bare_asset, db):
    seller, buyer = make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 10)
    seller.order(bare_asset, "sell", 4, price=9_900)
    buyer.order(bare_asset, "buy", 4, price=9_900)
    t = db.execute(select(Trade).where(Trade.seller_id == seller.id)).scalar_one()
    assert t.seller_fee == 80 and not t.seller_fee_waived  # 0.2% of 39,600 (rounded up)


def test_fee_rate_is_locked_when_order_is_placed(make_user, bare_asset, db):
    seller, buyer = make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 10)
    resting = seller.order(bare_asset, "sell", 5, price=10_100).json()["order"]
    assert resting["fee_bps"] == 20
    a = db.get(MarketAsset, bare_asset)
    a.new_video_until = utcnow() + timedelta(hours=48)  # surcharge window opens afterwards
    db.commit()
    r = buyer.order(bare_asset, "buy", 5, price=10_100).json()
    t = db.get(Trade, r["fills"][0]["id"])
    assert r["order"]["fee_bps"] == 100
    assert t.buyer_fee == 505 and t.seller_fee == 101  # each side pays its own locked rate
    assert reconcile(db).status == "ok"


def _api(handler):
    return YouTubeDataAPIProvider("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_new_video_opens_surcharge_window_and_notifies_holders(make_user, db):
    ch = register_channel(db, youtube_channel_id="UC" + "v" * 22, title="Video channel",
                          data_source="youtube_api", category=None, actor_id=None)
    db.commit()
    videos = {"n": 10}

    def handler(req):
        if req.url.path.endswith("/channels"):
            return httpx.Response(200, json={"items": [{"id": ch.youtube_channel_id, "snippet": {"title": "V"},
                                  "statistics": {"viewCount": "1000", "subscriberCount": "100",
                                                 "videoCount": str(videos["n"])}}]})
        return httpx.Response(200, json={"items": []})

    now = utcnow()
    collector.collect_all(db, now=now - timedelta(days=1), api=_api(handler))
    db.commit()
    a = approve_listing(db, channel_id=ch.id, symbol="VID", initial_price=5_000, shares_outstanding=100_000,
                        actor_id=admin_id(db))
    db.commit()
    holder = make_user()
    holder.order(a.id, "buy", 3)
    collector.collect_all(db, now=now, api=_api(handler))  # same count -> no surcharge
    db.commit()
    db.refresh(a)
    assert a.new_video_until is None
    videos["n"] = 11  # a new upload
    res = collector.collect_all(db, now=now + timedelta(minutes=5), api=_api(handler))
    db.commit()
    db.refresh(a)
    assert res.get("new_videos") == ["VID"]
    assert a.new_video_until > utcnow() + timedelta(hours=47)
    assert holder.get(f"/api/assets/{a.id}").json()["fee_surcharge_reason"] == "new_video"
    notes = holder.get("/api/notifications").json()
    assert any(n["kind"] == "new_video" for n in notes)


# ---------------------------------------------------------------- approval-only listings
def test_listing_requires_an_admin_decision(make_user, db):
    u = make_user()
    ch = register_channel(db, youtube_channel_id="DEMO_needs_ok", title="[DEMO] x", data_source="demo",
                          category=None, actor_id=None)
    db.commit()
    for actor in (None, u.id):
        with pytest.raises(AppError) as e:
            approve_listing(db, channel_id=ch.id, symbol="NOPE", initial_price=1_000,
                            shares_outstanding=10_000, actor_id=actor)
        assert e.value.code == "FORBIDDEN"
    db.rollback()
    assert db.get(Channel, ch.id).listing_status == "pending"


def test_user_listing_request_flow(make_user, make_admin, db, monkeypatch):
    u = make_user()
    cid = "UC" + "r" * 22
    r = u.post("/api/listing-requests", json={"channel": f"https://www.youtube.com/channel/{cid}",
                                              "note": "인기 채널이에요"})
    assert r.status_code == 201, r.text
    assert r.json()["listing_status"] == "pending"
    dup = u.post("/api/listing-requests", json={"channel": cid})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "ALREADY_REQUESTED"
    # Without an API key a handle is looked up on the public channel page.
    from app.services import channel_lookup
    monkeypatch.setattr(channel_lookup, "lookup_public_page", lambda h: None)
    h = u.post("/api/listing-requests", json={"channel": "@somehandle"})
    assert h.status_code == 404 and h.json()["error"]["code"] == "CHANNEL_NOT_FOUND"
    assert u.post("/api/listing-requests", json={"channel": "not a channel"}).status_code == 422
    assert [x["youtube_channel_id"] for x in u.get("/api/listing-requests").json()] == [cid]

    # Pending channels are not tradable or listed
    assert u.get("/api/assets").json() == []

    admin = make_admin()
    rows = admin.get("/api/admin/channels?listing_status=pending").json()
    assert rows[0]["requested_by"] == u.username and rows[0]["request_note"] == "인기 채널이에요"
    ok = admin.post(f"/api/admin/channels/{rows[0]['id']}/approve", json={
        "symbol": "REQ", "initial_price": 5_000, "shares_outstanding": 100_000, "reason": "valid request",
        "celebrity_new_channel": True})
    assert ok.status_code == 201, ok.text
    asset = db.get(MarketAsset, ok.json()["asset_id"])
    assert asset.celebrity_until is not None
    assert any(n["kind"] == "listing_approved" for n in u.get("/api/notifications").json())
    m = {x["code"]: x for x in u.get("/api/missions").json()}
    assert m["listing_scout"]["claimable"] is True


def test_listing_request_with_handle_uses_api(make_user, db, monkeypatch):
    from app.api import social

    cid = "UC" + "h" * 22

    def handler(req):
        if "forHandle" in req.url.params:
            assert req.url.params["forHandle"] == "@famous"
            return httpx.Response(200, json={"items": [{"id": cid}]})
        return httpx.Response(200, json={"items": [{"id": cid, "snippet": {"title": "Famous Person"},
                                                    "statistics": {"viewCount": "5", "videoCount": "1",
                                                                   "subscriberCount": "9"}}]})

    monkeypatch.setattr(social, "get_api_provider", lambda: _api(handler))
    u = make_user()
    r = u.post("/api/listing-requests", json={"channel": "https://www.youtube.com/@famous"})
    assert r.status_code == 201, r.text
    assert r.json()["youtube_channel_id"] == cid and r.json()["title"] == "Famous Person"


def test_listing_requests_rate_limited(make_user, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "rl_listing_requests_per_user", (2, 86400))
    u = make_user()
    codes = [u.post("/api/listing-requests", json={"channel": "UC" + c * 22}).status_code for c in "abc"]
    assert codes == [201, 201, 429]


# ---------------------------------------------------------------- chat
def test_chat_post_and_read(make_user, app, asset):
    from fastapi.testclient import TestClient

    a, b = make_user(), make_user()
    r = a.post("/api/chat/global/messages", json={"body": "  안녕하세요‮  <script>alert(1)</script> "})
    assert r.status_code == 201, r.text
    assert r.json()["body"] == "안녕하세요 <script>alert(1)</script>"  # bidi override stripped, stored as text
    b.post(f"/api/chat/asset:{asset}/messages", json={"body": "이 종목 어때요?"})
    anon = TestClient(app)
    msgs = anon.get("/api/chat/global/messages").json()
    assert [m["username"] for m in msgs] == [a.username]
    assert anon.get(f"/api/chat/asset:{asset}/messages").json()[0]["body"] == "이 종목 어때요?"
    last = msgs[-1]["id"]
    a.post("/api/chat/global/messages", json={"body": "두번째"})
    newer = anon.get(f"/api/chat/global/messages?after_id={last}").json()
    assert [m["body"] for m in newer] == ["두번째"]
    # anonymous users cannot post
    assert anon.post("/api/chat/global/messages", json={"body": "x"},
                     headers={"X-Requested-With": "yex"}).status_code == 401


def test_chat_validation_and_limits(make_user, db, monkeypatch):
    from app.config import get_settings

    u = make_user()
    assert u.post("/api/chat/global/messages", json={"body": "   "}).json()["error"]["code"] == "EMPTY_MESSAGE"
    assert u.post("/api/chat/global/messages", json={"body": "x" * 301}).json()["error"]["code"] == "MESSAGE_TOO_LONG"
    assert u.post("/api/chat/nope/messages", json={"body": "hi"}).status_code == 404
    assert u.post("/api/chat/asset:999/messages", json={"body": "hi"}).status_code == 404
    assert u.post("/api/chat/global/messages", json={"body": "same"}).status_code == 201
    dup = u.post("/api/chat/global/messages", json={"body": "same"})
    assert dup.json()["error"]["code"] == "DUPLICATE_MESSAGE"
    monkeypatch.setattr(get_settings(), "rl_chat_per_user", (3, 60))
    codes = [u.post("/api/chat/global/messages", json={"body": f"m{i}"}).status_code for i in range(4)]
    assert 429 in codes
    r = make_user()
    db.get(User, r.id).status = "restricted"
    db.commit()
    assert r.post("/api/chat/global/messages", json={"body": "hi"}).status_code == 403


def test_admin_can_hide_chat_and_users_cannot(make_user, make_admin, db):
    u, other = make_user(), make_user()
    mid = u.post("/api/chat/global/messages", json={"body": "광고 스팸"}).json()["id"]
    assert other.post(f"/api/admin/chat/{mid}/hide", json={"reason": "spam"}).status_code == 403
    rep = other.post("/api/reports", json={"target_type": "chat_message", "target_id": str(mid), "reason": "스팸입니다"})
    assert rep.status_code == 201
    admin = make_admin()
    assert admin.post(f"/api/admin/chat/{mid}/hide", json={"reason": "spam ad"}).status_code == 200
    assert u.get("/api/chat/global/messages").json() == []
    logs = admin.get("/api/admin/audit-logs?action=chat.").json()
    assert logs[0]["action"] == "chat.hide" and logs[0]["actor_id"] == admin.id


# ---------------------------------------------------------------- notifications
def test_fills_notify_taker_and_maker(make_user, bare_asset, db):
    seller, buyer = make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 5)
    seller.order(bare_asset, "sell", 5, price=10_000)
    seller.post("/api/notifications/read", json={"all": True})
    buyer.order(bare_asset, "buy", 5, price=10_000)
    s_notes = seller.get("/api/notifications?unread_only=true").json()
    b_notes = buyer.get("/api/notifications?unread_only=true").json()
    assert [n["kind"] for n in s_notes] == ["order_filled"] and "매도 체결" in s_notes[0]["title"]
    assert [n["kind"] for n in b_notes] == ["order_filled"] and "매수 체결" in b_notes[0]["title"]
    assert buyer.get("/api/notifications/unread-count").json() == {"unread": 1}


def test_notifications_are_private(make_user, bare_asset, db):
    a, b = make_user(), make_user()
    give_shares(db, a.id, bare_asset, 2)  # a gets an order_filled notification
    note_id = a.get("/api/notifications").json()[0]["id"]
    assert b.get("/api/notifications").json() == []
    assert b.post("/api/notifications/read", json={"ids": [note_id]}).json() == {"marked": 0}
    assert a.get("/api/notifications/unread-count").json()["unread"] >= 1
    assert a.post("/api/notifications/read", json={"ids": [note_id]}).json() == {"marked": 1}


def test_halt_notifies_holders(make_user, make_admin, asset):
    holder, other = make_user(), make_user()
    holder.order(asset, "buy", 2)
    make_admin().post(f"/api/admin/assets/{asset}/halt", json={"reason": "정기 점검"})
    assert any(n["kind"] == "asset_halted" for n in holder.get("/api/notifications").json())
    assert other.get("/api/notifications").json() == []


# ---------------------------------------------------------------- missions
def test_mission_claim_pays_once(make_user, asset, db):
    u = make_user()
    r = u.post("/api/missions/first_trade/claim")
    assert r.status_code == 409 and r.json()["error"]["code"] == "MISSION_NOT_COMPLETED"
    u.order(asset, "buy", 1)
    before = acct(db, u.id).cash_balance
    r = u.post("/api/missions/first_trade/claim")
    assert r.status_code == 201 and r.json()["reward"] == 20_000
    assert acct(db, u.id).cash_balance == before + 20_000
    again = u.post("/api/missions/first_trade/claim")
    assert again.status_code == 409 and again.json()["error"]["code"] == "MISSION_ALREADY_CLAIMED"
    entry = db.execute(select(LedgerEntry).where(LedgerEntry.entry_type == "mission_reward")).scalar_one()
    assert entry.amount == 20_000 and entry.reference_id == "first_trade:once"
    m = {x["code"]: x for x in u.get("/api/missions").json()}
    assert m["first_trade"]["claimed"] and not m["first_trade"]["claimable"]
    assert any(n["kind"] == "mission_reward" for n in u.get("/api/notifications").json())
    run = reconcile(db)
    assert run.status == "ok" and run.totals_json["by_entry_type"]["mission_reward"] == 20_000
    assert u.post("/api/missions/unknown/claim").status_code == 404


def test_concurrent_mission_claims_pay_once(make_user, asset, db):
    u = make_user()
    u.order(asset, "buy", 1)
    barrier = threading.Barrier(8)
    codes = []

    def go():
        barrier.wait()
        codes.append(u.post("/api/missions/daily_trade/claim").status_code)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [201] + [409] * 7, codes
    assert db.execute(select(func.count(UserMission.id))).scalar_one() == 1
    assert reconcile(db).status == "ok"


def test_daily_mission_resets_next_day(make_user, asset, db, monkeypatch):
    from app.services import missions

    u = make_user()
    u.order(asset, "buy", 1)
    assert u.post("/api/missions/daily_trade/claim").status_code == 201
    assert u.post("/api/missions/daily_trade/claim").status_code == 409
    tomorrow = utcnow() + timedelta(days=1)
    monkeypatch.setattr(missions, "utcnow", lambda: tomorrow)
    # New day: no trade yet today -> not completed
    assert u.post("/api/missions/daily_trade/claim").json()["error"]["code"] == "MISSION_NOT_COMPLETED"


def test_restricted_users_cannot_claim(make_user, asset, db):
    u = make_user()
    u.order(asset, "buy", 1)
    db.get(User, u.id).status = "restricted"
    db.commit()
    assert u.post("/api/missions/first_trade/claim").status_code == 403


# ---------------------------------------------------------------- rankings
def test_rankings_by_market_cap_turnover_volume(make_user, db):
    small = list_demo_asset(db, symbol="DSMALL", price=1_000, shares=10_000)
    big = list_demo_asset(db, symbol="DBIG", price=20_000, shares=1_000_000)
    u = make_user()
    u.order(small, "buy", 30)  # more shares, less value
    u.order(big, "buy", 5)
    caps = make_user().get("/api/rankings?metric=market_cap").json()
    assert [a["symbol"] for a in caps] == ["DBIG", "DSMALL"]
    assert caps[0]["market_cap"] == caps[0]["current_price"] * 1_000_000
    turnover = u.get("/api/rankings?metric=turnover").json()
    assert turnover[0]["symbol"] == "DBIG" and turnover[0]["turnover_24h"] > turnover[1]["turnover_24h"]
    volume = u.get("/api/rankings?metric=volume").json()
    assert volume[0]["symbol"] == "DSMALL" and volume[0]["volume_24h"] == 30
    assert u.get("/api/assets?sort=market_cap").json()[0]["symbol"] == "DBIG"


def test_expert_picks_need_two_experts_and_hide_identities(make_user, db):
    a1 = list_demo_asset(db, symbol="DPICK", price=1_000, shares=100_000)
    a2 = list_demo_asset(db, symbol="DSOLO", price=1_000, shares=100_000)
    experts = [make_user() for _ in range(3)]
    for e in experts:
        for _ in range(5):  # trading history requirement
            e.order(a1, "buy", 1)
    experts[0].order(a2, "buy", 3)  # only one expert -> must not be shown
    picks = make_user().get("/api/rankings/expert-picks").json()
    assert [p["asset"]["symbol"] for p in picks] == ["DPICK"]
    assert picks[0]["expert_buyers"] == 3 and picks[0]["net_buy_notional"] > 0
    assert set(picks[0]) == {"rank", "expert_buyers", "net_buy_notional", "asset"}  # no usernames
