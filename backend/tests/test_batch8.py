"""Operator tools: delisting with payouts, market-wide halt/resume, overview, user management."""

from sqlalchemy import func, select

from app.models import Channel, LedgerEntry, MarketAsset, Order, Position
from app.services import reconciliation


def test_delisting_pays_holders_and_keeps_books_exact(make_admin, make_user, db, asset):
    admin, u = make_admin(), make_user()
    assert u.order(asset, "buy", 20).json()["order"]["status"] == "filled"
    assert u.order(asset, "buy", 5, price=1).status_code in (201, 409, 422)  # a resting order, if allowed
    a = db.get(MarketAsset, asset)
    db.refresh(a)
    price = a.current_price
    cash_before = u.get("/api/account").json()["cash_balance"]
    r = admin.post(f"/api/admin/assets/{asset}/delist", json={"reason": "채널 운영 종료"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["settlement_price"] == price and out["holders_paid"] >= 1
    db.expire_all()
    acct = u.get("/api/account").json()
    assert acct["cash_balance"] >= cash_before + 20 * price
    assert db.get(Channel, db.get(MarketAsset, asset).channel_id).listing_status == "delisted"
    assert db.get(MarketAsset, asset).trading_status == "halted"
    resting = db.execute(select(func.count(Order.id)).where(Order.asset_id == asset,
                                                            Order.status.in_(("open", "partially_filled")))).scalar_one()
    assert resting == 0
    funded = db.execute(select(func.count(LedgerEntry.id)).where(LedgerEntry.reference_type == "delisting")).scalar_one()
    assert funded == 1
    held = db.execute(select(func.coalesce(func.sum(Position.quantity), 0)).join(
        MarketAsset, MarketAsset.id == Position.asset_id).where(Position.asset_id == asset)).scalar_one()
    assert held == db.get(MarketAsset, asset).shares_outstanding
    run = reconciliation.reconcile(db)
    assert run.status == "ok", run.mismatches_json
    # gone from the market, cannot be traded or delisted twice
    assert all(x["id"] != asset for x in u.get("/api/assets").json())
    assert u.order(asset, "buy", 1).status_code >= 400
    assert admin.post(f"/api/admin/assets/{asset}/delist", json={}).status_code == 409
    assert any(n["kind"] == "asset_delisted" for n in u.get("/api/notifications").json())


def test_delisting_is_admin_only(make_user, asset):
    assert make_user().post(f"/api/admin/assets/{asset}/delist", json={}).status_code == 403


def test_halt_all_and_resume_all(make_admin, db, asset):
    admin = make_admin()
    r = admin.post("/api/admin/market/halt-all", json={"reason": "긴급 점검"})
    assert r.status_code == 200 and r.json()["halted"] >= 1
    db.expire_all()
    assert db.get(MarketAsset, asset).trading_status == "halted"
    r = admin.post("/api/admin/market/resume-all", json={})
    assert r.status_code == 200 and r.json()["resumed"] >= 1
    db.expire_all()
    assert db.get(MarketAsset, asset).trading_status == "active"


def test_overview_and_user_management(make_admin, make_user, asset):
    admin, u = make_admin(), make_user()
    u.order(asset, "buy", 3)
    o = admin.get("/api/admin/overview").json()
    assert o["users"]["total"] >= 1 and o["listed"] >= 1 and o["player_trades_24h"] >= 1
    rows = admin.get("/api/admin/users", params={"q": u.username[:6]}).json()
    me = next(r for r in rows if r["username"] == u.username)
    assert me["equity"] == me["cash"] + me["holdings_value"] and me["holdings_value"] > 0
    r = admin.post("/api/admin/adjustments", json={"user_id": me["id"], "amount": 50_000,
                                                   "reason": "이벤트 당첨 보상 지급", "idempotency_key": "evt-0001-x"})
    assert r.status_code == 201
    assert admin.post(f"/api/admin/users/{me['id']}/status", json={"status": "restricted", "reason": "도배"}).status_code == 200
    assert next(r for r in admin.get("/api/admin/users").json() if r["id"] == me["id"])["status"] == "restricted"


def test_levels_and_tiers(make_user, asset):
    from app.services.progress import level_for, tier_for

    assert level_for(0)[0] == 1 and level_for(99)[0] == 1 and level_for(100)[0] == 2 and level_for(300)[0] == 3
    assert tier_for(1_000_000)[0] == "브론즈" and tier_for(2_500_000) == ("골드", 5_000_000)
    assert tier_for(500_000_000) == ("마스터", None)
    u = make_user()
    a0 = u.get("/api/account").json()
    assert a0["level"] == 1 and a0["xp"] == 0 and a0["tier"]
    u.order(asset, "buy", 5)
    a1 = u.get("/api/account").json()
    assert a1["xp"] >= 10 and a1["next_level_xp"] > a1["level_start_xp"]
