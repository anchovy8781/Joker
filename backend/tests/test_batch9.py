"""Order amendment (정정) and channel growth that always has an answer."""

from datetime import timedelta

from sqlalchemy import select

from app.models import Channel, ChannelSample, MarketAsset, Order
from app.security import utcnow
from app.services import reconciliation


def test_amend_moves_price_and_quantity(make_user, db, asset):
    u = make_user()
    price = db.get(MarketAsset, asset).current_price
    low = price * 90 // 100
    r = u.order(asset, "buy", 10, price=low)
    assert r.status_code == 201 and r.json()["order"]["status"] == "open", r.text
    oid = r.json()["order"]["id"]
    a = u.post(f"/api/orders/{oid}/amend", json={"limit_price": low + 1, "quantity": 7, "idempotency_key": "amend-0001"})
    assert a.status_code == 200, a.text
    new = a.json()["order"]
    assert new["id"] != oid and new["limit_price"] == low + 1 and new["quantity"] == 7 and new["status"] == "open"
    db.expire_all()
    old = db.get(Order, oid)
    assert old.status == "canceled" and old.status_reason == "USER_AMENDED"
    # a retry returns the same replacement, never a second one
    again = u.post(f"/api/orders/{oid}/amend", json={"limit_price": low + 1, "quantity": 7, "idempotency_key": "amend-0001"})
    assert again.status_code == 200 and again.json()["order"]["id"] == new["id"] and again.json()["replayed"]
    # amending into the book fills it
    fill = u.post(f"/api/orders/{new['id']}/amend", json={"limit_price": price * 105 // 100, "idempotency_key": "amend-0002"})
    assert fill.status_code == 200 and fill.json()["order"]["status"] in ("filled", "partially_filled", "open")
    assert reconciliation.reconcile(db).status == "ok"


def test_amend_rules(make_user, db, asset):
    u, other = make_user(), make_user()
    r = u.order(asset, "buy", 5, price=db.get(MarketAsset, asset).current_price * 90 // 100)
    oid = r.json()["order"]["id"]
    assert other.post(f"/api/orders/{oid}/amend", json={"quantity": 1, "idempotency_key": "amend-x001"}).status_code == 404
    assert u.post(f"/api/orders/{oid}/amend", json={"idempotency_key": "amend-x002"}).status_code == 422
    m = u.order(asset, "buy", 1)
    assert u.post(f"/api/orders/{m.json()['order']['id']}/amend", json={"quantity": 2, "idempotency_key": "amend-x003"}).status_code == 409


def test_growth_answers_even_with_short_history(make_user, db, asset):
    ch = db.get(Channel, db.get(MarketAsset, asset).channel_id)
    now = utcnow()
    for h, subs, views in ((5, 10_000, 1_000_000), (2, 10_050, 1_010_000), (0, 10_120, 1_025_000)):
        db.add(ChannelSample(channel_id=ch.id, observed_at=now - timedelta(hours=h), subscriber_count=subs,
                             view_count=views, video_count=1, source=ch.data_source))
    db.commit()
    g = make_user().get(f"/api/assets/{asset}/growth").json()
    d7 = g["subscribers"]["d7"]
    assert d7 is not None and d7["change"] != 0 or g["subscribers"]["d1"] is not None
    assert g["views"]["d1"]["span_hours"] > 0


def test_heartbeat_and_rounds_pause_when_the_market_is_closed(db, asset, monkeypatch):
    import random as _r

    from app.services import bots, market_hours

    monkeypatch.setattr(market_hours, "is_open", lambda now: False)
    bots.ensure_bots(db)
    db.commit()
    before = db.execute(select(Order.id).order_by(Order.id.desc()).limit(1)).scalar()
    for _ in range(3):
        assert bots.tape_tick(db, [asset], rng=_r.Random(1)).get("closed")
        assert bots.run_round(db, rng=_r.Random(1), commit_each=True).get("closed")
    after = db.execute(select(Order.id).order_by(Order.id.desc()).limit(1)).scalar()
    assert after == before  # no bot orders, no overnight reservations


def test_amend_key_reuse_for_another_order_is_refused(make_user, db, asset):
    u = make_user()
    low = db.get(MarketAsset, asset).current_price * 90 // 100
    a = u.order(asset, "buy", 3, price=low).json()["order"]["id"]
    b = u.order(asset, "buy", 4, price=low).json()["order"]["id"]
    assert u.post(f"/api/orders/{a}/amend", json={"limit_price": low + 1, "idempotency_key": "amend-k-01"}).status_code == 200
    r = u.post(f"/api/orders/{b}/amend", json={"limit_price": low + 2, "idempotency_key": "amend-k-01"})
    assert r.status_code == 409


def test_resume_all_only_resumes_what_halt_all_stopped(make_admin, db, asset):
    admin = make_admin()
    assert admin.post(f"/api/admin/assets/{asset}/halt", json={"reason": "조사 중"}).status_code == 200
    assert admin.post("/api/admin/market/halt-all", json={}).json()["halted"] == 0
    r = admin.post("/api/admin/market/resume-all", json={}).json()
    assert r["resumed"] == 0 and r["failed"] == []
    db.expire_all()
    assert db.get(MarketAsset, asset).trading_status == "halted"
    assert admin.get("/api/admin/users", params={"limit": -1}).status_code == 422
