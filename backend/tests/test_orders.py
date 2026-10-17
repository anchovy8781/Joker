"""Required tests 3, 4, 6, 7, 8, 9, 18, 19 + engine rules."""

from sqlalchemy import func, select

from app.models import Account, Order, Position, Trade
from app.services.fees import buy_reservation, fee_for
from tests.conftest import give_shares, open_orders


def acct(db, uid):
    db.expire_all()
    return db.get(Account, uid)


def pos(db, uid, aid):
    db.expire_all()
    return db.get(Position, (uid, aid))


def test_03_buy_larger_than_balance_rejected(make_user, bare_asset, db):
    u = make_user()
    # 100 shares @ 10,500 = 1,050,000 + fee > 1,000,000
    r = u.order(bare_asset, "buy", 100, price=10_500)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_FUNDS"
    assert db.execute(select(func.count(Order.id)).where(Order.user_id == u.id)).scalar_one() == 0
    a = acct(db, u.id)
    assert a.cash_balance == 1_000_000 and a.reserved_cash == 0


def test_03b_reserved_cash_cannot_be_reused(make_user, bare_asset, db):
    u = make_user()
    assert u.order(bare_asset, "buy", 90, price=10_000).status_code == 201  # rests, reserves ~902k
    r = u.order(bare_asset, "buy", 10, price=10_000)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_FUNDS"


def test_04_sell_more_than_held_rejected(make_user, asset, db):
    u = make_user()
    r = u.order(asset, "sell", 1, price=10_000)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_SHARES"
    give_shares(db, u.id, asset, 10)
    assert u.order(asset, "sell", 11, price=10_000).json()["error"]["code"] == "INSUFFICIENT_SHARES"
    # Reserved shares cannot be sold twice
    assert u.order(asset, "sell", 7, price=10_600).status_code == 201
    r = u.order(asset, "sell", 4, price=10_600)
    assert r.status_code == 409 and r.json()["error"]["details"]["available"] == 3


def test_06_resent_order_does_not_duplicate(make_user, asset, db):
    u = make_user()
    first = u.order(asset, "buy", 5, key="retry-key-0001")
    assert first.status_code == 201
    for _ in range(3):
        again = u.order(asset, "buy", 5, key="retry-key-0001")
        assert again.status_code == 200 and again.json()["replayed"] is True
        assert again.json()["order"]["id"] == first.json()["order"]["id"]
        assert len(again.json()["fills"]) == len(first.json()["fills"])
    assert db.execute(select(func.count(Order.id)).where(Order.user_id == u.id)).scalar_one() == 1
    assert db.execute(select(func.sum(Trade.quantity)).where(Trade.buyer_id == u.id)).scalar_one() == 5
    # Same key, different payload -> conflict, not a silent replay
    r = u.order(asset, "buy", 6, key="retry-key-0001")
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    # Keys are per user: another user may reuse the string
    assert make_user().order(asset, "buy", 1, key="retry-key-0001").status_code == 201


def test_07_partial_fill_remaining_quantity(make_user, bare_asset, db):
    seller, buyer = make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 30)
    assert seller.order(bare_asset, "sell", 30, price=10_100).status_code == 201
    r = buyer.order(bare_asset, "buy", 12, price=10_100).json()
    assert r["order"]["status"] == "filled" and r["fills"][0]["quantity"] == 12
    resting = db.execute(select(Order).where(Order.user_id == seller.id, Order.side == "sell")).scalar_one()
    assert resting.status == "partially_filled" and resting.remaining_quantity == 18
    assert pos(db, seller.id, bare_asset).reserved_quantity == 18
    # Buyer's limit partially filled: rests with exact remainder
    r = buyer.order(bare_asset, "buy", 25, price=10_100).json()
    assert r["order"]["status"] == "partially_filled"
    assert r["order"]["filled_quantity"] == 18 and r["order"]["remaining_quantity"] == 7
    db.expire_all()
    assert db.get(Order, resting.id).status == "filled"
    assert pos(db, seller.id, bare_asset).quantity == 0
    assert pos(db, buyer.id, bare_asset).quantity == 30


def test_08_cancel_returns_reserved_cash_and_shares(make_user, bare_asset, db):
    u = make_user()
    r = u.order(bare_asset, "buy", 20, price=9_900).json()
    reserved = buy_reservation(20, 9_900)
    assert r["order"]["reserved_cash"] == reserved
    assert acct(db, u.id).reserved_cash == reserved
    c = u.post(f"/api/orders/{r['order']['id']}/cancel")
    assert c.status_code == 200 and c.json()["status"] == "canceled"
    a = acct(db, u.id)
    assert a.reserved_cash == 0 and a.cash_balance == 1_000_000
    # Cancel twice -> refused, no double refund
    assert u.post(f"/api/orders/{r['order']['id']}/cancel").status_code == 409
    assert acct(db, u.id).cash_balance == 1_000_000

    # Shares: partially filled sell, then cancel returns only the unfilled part
    give_shares(db, u.id, bare_asset, 10)
    s = u.order(bare_asset, "sell", 10, price=10_200).json()
    assert pos(db, u.id, bare_asset).reserved_quantity == 10
    other = make_user()
    other.order(bare_asset, "buy", 4, price=10_200)
    u.post(f"/api/orders/{s['order']['id']}/cancel")
    p = pos(db, u.id, bare_asset)
    assert p.quantity == 6 and p.reserved_quantity == 0


def test_08b_partial_buy_cancel_releases_exact_remainder(make_user, bare_asset, db):
    seller, buyer = make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 5)
    seller.order(bare_asset, "sell", 5, price=9_950)
    r = buyer.order(bare_asset, "buy", 20, price=10_000).json()  # 5 fill @ 9,950 (price improvement)
    assert r["order"]["status"] == "partially_filled"
    notional = 5 * 9_950
    spent = notional + fee_for(notional)
    a = acct(db, buyer.id)
    assert a.cash_balance == 1_000_000 - spent
    assert a.reserved_cash == buy_reservation(20, 10_000) - spent
    buyer.post(f"/api/orders/{r['order']['id']}/cancel")
    a = acct(db, buyer.id)
    assert a.reserved_cash == 0 and a.cash_balance == 1_000_000 - spent


def test_09_fees_exact(make_user, bare_asset, db):
    seller, buyer = make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 10)
    seller_cash0 = acct(db, seller.id).cash_balance
    seller.order(bare_asset, "sell", 7, price=10_000)
    r = buyer.order(bare_asset, "buy", 7, price=10_000).json()
    t = db.get(Trade, r["fills"][0]["id"])
    assert t.quantity * t.execution_price == 70_000
    assert t.buyer_fee == 140 and t.seller_fee == 140  # 0.2%
    assert acct(db, buyer.id).cash_balance == 1_000_000 - 70_000 - 140
    assert acct(db, seller.id).cash_balance == seller_cash0 + 70_000 - 140
    # Rounding is always up to a whole credit (cannot dodge fees by tiny orders)
    assert fee_for(100) == 1 and fee_for(499) == 1 and fee_for(500) == 1 and fee_for(501) == 2
    assert fee_for(0) == 0


def test_09b_client_cannot_set_fee_or_price_fields(make_user, asset):
    u = make_user()
    for extra in ({"fee": 0}, {"execution_price": 1}, {"buyer_fee": 0}, {"user_id": 1}):
        body = {"asset_id": asset, "side": "buy", "order_type": "market", "quantity": 1,
                "idempotency_key": "extra-field-1", **extra}
        r = u.post("/api/orders", json=body)
        assert r.status_code == 422, extra


def test_18_price_then_time_priority(make_user, bare_asset, db):
    s1, s2, s3, buyer = make_user(), make_user(), make_user(), make_user()
    for s in (s1, s2, s3):
        give_shares(db, s.id, bare_asset, 10)
    o_late_same = None
    o1 = s1.order(bare_asset, "sell", 10, price=10_100).json()["order"]["id"]   # first at 10,100
    o2 = s2.order(bare_asset, "sell", 10, price=10_050).json()["order"]["id"]   # better price
    o_late_same = s3.order(bare_asset, "sell", 10, price=10_100).json()["order"]["id"]  # later at 10,100
    r = buyer.order(bare_asset, "buy", 25, price=10_100).json()
    fills = [(db.get(Trade, f["id"]).sell_order_id, f["quantity"], f["execution_price"]) for f in r["fills"]]
    assert fills == [(o2, 10, 10_050), (o1, 10, 10_100), (o_late_same, 5, 10_100)]

    # Bids: higher price first, then earlier
    b1, b2, b3, seller = make_user(), make_user(), make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 30)
    x1 = b1.order(bare_asset, "buy", 5, price=9_900).json()["order"]["id"]
    x2 = b2.order(bare_asset, "buy", 5, price=9_950).json()["order"]["id"]
    x3 = b3.order(bare_asset, "buy", 5, price=9_900).json()["order"]["id"]
    r = seller.order(bare_asset, "sell", 12, price=9_900).json()
    fills = [(db.get(Trade, f["id"]).buy_order_id, f["quantity"], f["execution_price"]) for f in r["fills"]]
    assert fills == [(x2, 5, 9_950), (x1, 5, 9_900), (x3, 2, 9_900)]


def test_19_market_order_without_counterparty_no_fake_fill(make_user, bare_asset, db):
    u = make_user()
    trades_before = db.execute(select(func.count(Trade.id))).scalar_one()
    r = u.order(bare_asset, "buy", 5)
    assert r.status_code == 201
    o = r.json()["order"]
    assert o["status"] == "rejected" and o["status_reason"] == "NO_LIQUIDITY" and r.json()["fills"] == []
    assert db.execute(select(func.count(Trade.id))).scalar_one() == trades_before
    a = acct(db, u.id)
    assert a.cash_balance == 1_000_000 and a.reserved_cash == 0
    # Market sell with no bids: same
    give_shares(db, u.id, bare_asset, 3)
    r = u.order(bare_asset, "sell", 3).json()
    assert r["order"]["status"] == "rejected" and pos(db, u.id, bare_asset).reserved_quantity == 0


def test_19b_market_order_price_protection_and_ioc(make_user, bare_asset, db):
    s1, s2, buyer = make_user(), make_user(), make_user()
    give_shares(db, s1.id, bare_asset, 5)
    give_shares(db, s2.id, bare_asset, 5)
    s1.order(bare_asset, "sell", 5, price=10_100)
    s2.order(bare_asset, "sell", 5, price=10_600)  # beyond the 5% market-order protection
    r = buyer.order(bare_asset, "buy", 10).json()
    assert r["order"]["status"] == "canceled"
    assert r["order"]["status_reason"] == "PARTIAL_FILL_REMAINDER_CANCELED"
    assert r["order"]["filled_quantity"] == 5
    assert [f["execution_price"] for f in r["fills"]] == [10_100]
    assert acct(db, buyer.id).reserved_cash == 0  # remainder reservation released


def test_limit_price_band_enforced(make_user, bare_asset):
    u = make_user()
    r = u.order(bare_asset, "buy", 1, price=20_000)
    assert r.status_code == 422 and r.json()["error"]["code"] == "PRICE_OUT_OF_BAND"
    assert u.order(bare_asset, "buy", 1, price=99).status_code == 422


def test_self_trade_prevented(make_user, bare_asset, db):
    u = make_user()
    give_shares(db, u.id, bare_asset, 5)
    u.order(bare_asset, "sell", 5, price=10_000)
    r = u.order(bare_asset, "buy", 5, price=10_000).json()
    assert r["fills"] == [] and r["order"]["status"] == "canceled"
    assert r["order"]["status_reason"] == "SELF_TRADE_PREVENTED"
    assert acct(db, u.id).reserved_cash == 0


def test_halted_asset_rejects_orders_but_allows_cancel(make_user, bare_asset, db):
    from app.services.listing import halt

    u = make_user()
    o = u.order(bare_asset, "buy", 1, price=9_900).json()["order"]
    halt(db, bare_asset, "test halt", None)
    db.commit()
    r = u.order(bare_asset, "buy", 1, price=9_900)
    assert r.status_code == 409 and r.json()["error"]["code"] == "TRADING_HALTED"
    assert u.post(f"/api/orders/{o['id']}/cancel").status_code == 200


def test_trade_history_matches_executions(make_user, asset, db):
    u = make_user()
    u.order(asset, "buy", 3)
    u.order(asset, "buy", 4)
    mine = u.get("/api/trades").json()
    db_trades = db.execute(select(Trade).where(Trade.buyer_id == u.id).order_by(Trade.id.desc())).scalars().all()
    assert [t["id"] for t in mine] == [t.id for t in db_trades]
    assert sum(t["quantity"] for t in mine) == 7 == pos(db, u.id, asset).quantity
    public = u.get(f"/api/assets/{asset}/trades").json()
    assert "buyer_id" not in public[0] and "seller_id" not in public[0]


def test_realized_pnl(make_user, bare_asset, db):
    seller, buyer = make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 10)  # bought @ 10,000 (+fee 0.2%)
    p = pos(db, seller.id, bare_asset)
    assert p.average_cost == 10_020  # (100,000 + 200) / 10
    seller.order(bare_asset, "sell", 10, price=10_500)
    buyer.order(bare_asset, "buy", 10, price=10_500)
    p = pos(db, seller.id, bare_asset)
    # proceeds 105,000 - fee 210 - cost 100,200 = 4,590
    assert p.quantity == 0 and p.realized_pnl == 4_590
    a = seller.get("/api/account").json()
    assert float(a["realized_pnl"]) == 4_590 and float(a["unrealized_pnl"]) == 0
    assert open_orders(db, bare_asset) == []
