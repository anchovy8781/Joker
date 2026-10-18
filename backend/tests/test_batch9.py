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


def test_korean_names_make_readable_symbols(make_admin, db, asset):
    from app.services.channel_lookup import romanize

    assert romanize("보따") == "BOTTA" and romanize("침착맨") == "CHIMCHAKMAEN" and romanize("우주하마") == "UJUHAMA"
    ch = db.get(Channel, db.get(MarketAsset, asset).channel_id)
    ch.title, ch.handle = "보따", "@보따"
    db.commit()
    admin = make_admin()
    r = admin.post(f"/api/admin/assets/{asset}/symbol", json={})
    assert r.status_code == 200 and r.json()["symbol"] == "BOTTA", r.text
    assert admin.post(f"/api/admin/assets/{asset}/symbol", json={"symbol": "x"}).status_code == 422
    assert admin.post(f"/api/admin/assets/{asset}/symbol", json={"symbol": "bodda2"}).json()["symbol"] == "BODDA2"


def test_desks_trade_and_flows_are_reported(make_user, db, asset):
    import random as _r
    from datetime import UTC, datetime

    from app.services import bots

    bots.ensure_bots(db)
    db.commit()
    t0 = datetime.now(UTC)
    for i in range(40):
        bots.tape_tick(db, [asset], now=t0 + timedelta(seconds=4 * i), rng=_r.Random(i))
    f = make_user().get(f"/api/assets/{asset}/flows").json()
    assert f["groups"] == ["개인", "외국인", "기관", "세력"] and f["days"]
    total = f["total"]
    assert total["기관"]["qty"] != 0 or total["외국인"]["qty"] != 0
    from app.services import reconciliation

    assert reconciliation.reconcile(db).status == "ok"


def test_spoof_bid_is_placed_then_withdrawn_and_shakeout_dumps(db, asset, monkeypatch):
    from datetime import UTC, datetime

    from app.models import User
    from app.services import bots, reconciliation

    bots.ensure_bots(db)
    db.commit()
    now = datetime.now(UTC)
    a = db.get(MarketAsset, asset)
    whales = db.execute(select(User).where(User.normalized_username.like("세력_%"))).scalars().all()
    bots._campaigns[asset] = {"shape": "distribute", "start": now, "end": now + timedelta(hours=1), "strength": 1.0}
    monkeypatch.setattr(bots._sysrand, "random", lambda: 0.0)
    bots._spoof(db, a, whales, now)
    sp = bots._spoofs[asset]
    assert db.get(Order, sp["order_id"]).status == "open"
    bots._spoof(db, a, whales, sp["end"] + timedelta(seconds=1))
    db.expire_all()
    assert db.get(Order, sp["order_id"]).status == "canceled" and asset not in bots._spoofs
    # 개미털기: an accumulating whale starts a dump, then buys back
    bots._campaigns[asset] = {"shape": "accumulate", "start": now, "end": now + timedelta(hours=1), "strength": 1.0}
    bots._shakeouts.pop(asset, None)
    import random as _r

    bots.desk_orders(a, _r.Random(1), now)  # random() == 0 starts the dump
    assert bots._shakeouts[asset]["stage"] == "dump"
    sells = [o for o in bots.desk_orders(a, _r.Random(2), now + timedelta(seconds=1)) if o[0] == "whale"]
    assert sells and all(side == "sell" for _, side, _ in sells)
    assert reconciliation.reconcile(db).status == "ok"
