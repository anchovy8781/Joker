"""Chat donation boxes and tips, price alerts, intraday fair value, new missions."""

import threading
from datetime import timedelta

from sqlalchemy import select, text

from app.db import session_factory
from app.models import Account, ChannelMetric, ChannelSample, DonationBox, MarketAsset, Notification, Order, PriceAlert
from app.security import utcnow
from app.services import alerts, intraday, reconciliation
from app.services.listing import get_market_maker, requote_market_maker
from tests.test_batch2 import _eligible


def _cash(db, uid):
    a = db.get(Account, uid)
    db.refresh(a)
    return a.cash_balance


def test_donation_box_in_chat_one_per_day(make_user, asset, db):
    owner, donor = make_user(), make_user()
    _eligible(db, donor, asset, "box-donor")
    r = owner.post("/api/chat/global/boxes", json={"title": "상장비 모아요", "goal": 50_000})
    assert r.status_code == 201, r.text
    box = r.json()
    assert box["state"] == "open" and box["owner"] == owner.username
    again = owner.post("/api/chat/global/boxes", json={"title": "하나 더", "goal": 10_000})
    assert again.status_code == 409 and again.json()["error"]["code"] == "DONATION_BOX_DAILY_LIMIT"
    assert owner.get("/api/donations").json()["my_boxes"]["can_open_today"] is False

    msgs = donor.get("/api/chat/global/messages").json()
    card = next(m for m in msgs if m["kind"] == "donation_box")
    assert card["box"]["id"] == box["id"] and card["box"]["goal"] == 50_000
    assert [b["id"] for b in donor.get("/api/chat/global/boxes").json()] == [box["id"]]

    before = _cash(db, owner.id)
    g = donor.post(f"/api/donation-boxes/{box['id']}/give", json={"amount": 20_000, "message": "화이팅"})
    assert g.status_code == 201, g.text
    assert g.json()["box"]["raised"] == 20_000
    assert _cash(db, owner.id) == before + 20_000
    over = donor.post(f"/api/donation-boxes/{box['id']}/give", json={"amount": 40_000})
    assert over.status_code == 409 and over.json()["error"]["details"]["remaining"] == 30_000
    full = donor.post(f"/api/donation-boxes/{box['id']}/give", json={"amount": 30_000})
    assert full.json()["box"]["state"] == "closed"
    assert donor.post(f"/api/donation-boxes/{box['id']}/give", json={"amount": 100}).json()["error"]["code"] == "DONATION_BOX_CLOSED"
    gifts = [m for m in donor.get("/api/chat/global/messages").json() if m["kind"] == "donation"]
    assert len(gifts) == 2 and "화이팅" in gifts[0]["body"]
    assert donor.get("/api/chat/global/boxes").json() == []
    assert reconciliation.reconcile(db).status == "ok"


def test_owner_cannot_fund_own_box_and_new_accounts_cannot_give(make_user, asset, db):
    owner, fresh = make_user(), make_user()
    _eligible(db, owner, asset, "own")
    box = owner.post("/api/chat/global/boxes", json={"title": "my box", "goal": 10_000}).json()
    assert owner.post(f"/api/donation-boxes/{box['id']}/give", json={"amount": 1_000}).json()["error"]["code"] == "SELF_DONATION"
    assert fresh.post(f"/api/donation-boxes/{box['id']}/give", json={"amount": 1_000}).json()["error"]["code"] == "ACCOUNT_TOO_NEW"


def test_box_goal_holds_under_concurrent_gifts(make_user, asset, db):
    owner = make_user()
    donors = [make_user() for _ in range(4)]
    for i, d in enumerate(donors):
        _eligible(db, d, asset, f"cbox{i}")
    box = owner.post("/api/chat/global/boxes", json={"title": "race", "goal": 30_000}).json()
    codes = []
    barrier = threading.Barrier(len(donors))

    def give(d):
        barrier.wait()
        codes.append(d.post(f"/api/donation-boxes/{box['id']}/give", json={"amount": 10_000}).status_code)
    threads = [threading.Thread(target=give, args=(d,)) for d in donors]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [201, 201, 201, 409]
    s = session_factory()()
    assert s.get(DonationBox, box["id"]).raised == 30_000
    s.close()


def test_tip_in_chat_is_announced(make_user, asset, db):
    a, b = make_user(), make_user()
    _eligible(db, a, asset, "tip-a")
    r = a.post("/api/chat/global/tip", json={"to": "@" + b.username, "amount": 5_000, "message": "고마워요"})
    assert r.status_code == 201, r.text
    last = a.get("/api/chat/global/messages").json()[-1]
    assert last["kind"] == "donation" and b.username in last["body"] and "5,000" in last["body"]
    assert any(n["kind"] == "donation_received" for n in b.get("/api/notifications").json())


def test_price_alert_fires_once(make_user, asset, db):
    u = make_user()
    price = db.get(MarketAsset, asset).current_price
    assert u.post("/api/price-alerts", json={"asset_id": asset, "direction": "above", "target_price": price}).json()["error"]["code"] == "ALREADY_REACHED"
    r = u.post("/api/price-alerts", json={"asset_id": asset, "direction": "above", "target_price": price + 500})
    assert r.status_code == 201
    assert alerts.check_all(db) == 0
    db.execute(text("UPDATE market_assets SET current_price = current_price + 600 WHERE id = :a"), {"a": asset})
    db.commit()
    assert alerts.check_all(db) == 1
    db.commit()
    assert alerts.check_all(db) == 0
    rows = u.get(f"/api/price-alerts?asset_id={asset}").json()
    assert rows[0]["active"] is False and rows[0]["triggered_at"]
    n = db.execute(select(Notification).where(Notification.user_id == u.id, Notification.kind == "price_alert")).scalars().all()
    assert len(n) == 1


def _seed_pace(db, channel_id: int, base_per_hour: float, now_per_hour: float):
    """7 days of daily view counts at base pace + an hour of live samples at the current pace."""
    now = utcnow()
    db.execute(text("DELETE FROM channel_metrics WHERE channel_id=:c AND metric_type='view_count'"), {"c": channel_id})
    for d in range(8, -1, -1):
        t = now - timedelta(days=d)
        db.add(ChannelMetric(channel_id=channel_id, metric_type="view_count", source="demo",
                             metric_value=int(10_000_000 + base_per_hour * 24 * (8 - d)), observed_at=t,
                             observed_date=t.date(), data_quality_status="ok"))
    for m in (60, 30, 0):
        db.add(ChannelSample(channel_id=channel_id, observed_at=now - timedelta(minutes=m), subscriber_count=None,
                             view_count=int(20_000_000 + now_per_hour * (60 - m) / 60), video_count=10, source="demo"))
    db.commit()


def test_intraday_fair_value_follows_live_view_pace(db, asset):
    a = db.get(MarketAsset, asset)
    _seed_pace(db, a.channel_id, base_per_hour=10_000, now_per_hour=20_000)  # twice the usual pace
    mm = get_market_maker(db)
    detail = intraday.compute(db, a, mm.id)
    assert 95 <= detail["yt_bps"] <= 110  # 150 × ln 2 ≈ 104
    assert detail["flow_bps"] == 0 and detail["adj_bps"] == detail["yt_bps"] + detail["subs_bps"]
    requote_market_maker(db, asset)
    db.commit()
    db.refresh(a)
    assert a.intraday_adj_bps == detail["adj_bps"]
    best_ask = db.execute(select(Order.limit_price).where(
        Order.asset_id == asset, Order.user_id == mm.id, Order.side == "sell", Order.status == "open")
        .order_by(Order.limit_price).limit(1)).scalar_one()
    assert best_ask > a.reference_price * (10_000 + 50) // 10_000  # centre moved up, not just the spread


def test_intraday_adjustment_is_clamped(db, asset):
    a = db.get(MarketAsset, asset)
    _seed_pace(db, a.channel_id, base_per_hour=1_000, now_per_hour=10_000_000)
    detail = intraday.compute(db, a, get_market_maker(db).id)
    assert detail["yt_bps"] == 250 and abs(detail["adj_bps"]) <= 400


def test_intraday_without_data_is_neutral(db, asset):
    a = db.get(MarketAsset, asset)
    db.execute(text("DELETE FROM channel_metrics WHERE channel_id=:c"), {"c": a.channel_id})
    db.commit()
    assert intraday.compute(db, a, get_market_maker(db).id)["adj_bps"] == 0


def test_daily_checkin_and_new_missions(make_user, db):
    u = make_user()
    codes = {m["code"]: m for m in u.get("/api/missions").json()}
    for c in ("daily_checkin", "daily_trade_5", "daily_chat", "diversify_5", "big_hand", "trades_50",
              "first_alert", "first_auto_order", "first_donation", "open_box"):
        assert c in codes
    assert codes["daily_checkin"]["claimable"] is True
    before = _cash(db, u.id)
    assert u.post("/api/missions/daily_checkin/claim").status_code == 201
    assert _cash(db, u.id) == before + 5_000
    assert u.post("/api/missions/daily_checkin/claim").status_code == 409
    assert u.post("/api/chat/global/messages", json={"body": "안녕"}).status_code == 201
    assert {m["code"]: m for m in u.get("/api/missions").json()}["daily_chat"]["claimable"] is True
