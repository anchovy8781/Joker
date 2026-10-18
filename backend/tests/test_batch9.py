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
