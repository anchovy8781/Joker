"""Korea Exchange-style rules: 호가단위 (tick sizes) and 변동성완화장치 (VI) call auctions."""

from datetime import timedelta

from sqlalchemy import select

from app.models import MarketAsset, Order, Trade, User
from app.security import utcnow
from app.services import krx, reconciliation
from app.services.listing import get_market_maker
from app.services.orders import lock_asset
from tests.conftest import give_shares


def test_tick_table_and_limits():
    assert [krx.tick_size(p) for p in (1_999, 2_000, 4_999, 5_000, 19_990, 20_000, 49_950, 50_000, 199_900,
                                       200_000, 499_500, 500_000)] == [1, 5, 5, 10, 10, 50, 50, 100, 100, 500, 500, 1000]
    assert krx.floor_tick(20_049) == 20_000 and krx.ceil_tick(19_995) == 20_000 and krx.ceil_tick(20_001) == 20_050
    assert krx.step(20_000, -1) == 19_990 and krx.step(19_990, 1) == 20_000
    lo, hi = krx.limits(10_000, 3000)
    assert (lo, hi) == (7_000, 13_000) and krx.on_tick(lo) and krx.on_tick(hi)
    lo, hi = krx.limits(16_100, 3000)  # 11,270 .. 20,930 -> 20,930 is off the 50 grid: 20,900
    assert (lo, hi) == (11_270, 20_900)


def test_off_tick_limit_is_refused_and_ladder_sits_on_ticks(make_user, db, asset):
    u = make_user()
    r = u.order(asset, "buy", 1, price=9_995)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_TICK"
    assert r.json()["error"]["details"] == {"tick": 10, "down": 9_990, "up": 10_000}
    assert u.order(asset, "buy", 1, price=9_990).status_code == 201
    mm = get_market_maker(db)
    prices = db.execute(select(Order.side, Order.limit_price).where(
        Order.asset_id == asset, Order.user_id == mm.id, Order.status == "open")).all()
    assert prices and all(krx.on_tick(p) for _, p in prices)
    bids = sorted(p for s, p in prices if s == "buy")
    asks = sorted(p for s, p in prices if s == "sell")
    assert len(set(bids)) == len(bids) and len(set(asks)) == len(asks) and max(bids) < min(asks)
    a = u.get(f"/api/assets/{asset}").json()
    a = a.get("asset", a)
    assert a["tick_size"] == 10 and a["upper_limit"] > a["current_price"] > a["lower_limit"] > 0


def test_vi_collects_orders_and_crosses_them_at_one_price(make_user, db, bare_asset):
    seller, buyer1, buyer2 = make_user(), make_user(), make_user()
    sid = db.execute(select(User.id).where(User.username == seller.username)).scalar_one()
    give_shares(db, sid, bare_asset, 10)
    a = lock_asset(db, bare_asset)
    a.current_price = 11_200  # +12% from the 10,000 reference: 정적 VI
    now = utcnow()
    assert krx.check_vi(db, a, now) == "static" and a.vi_until > now
    db.commit()
    # during the VI nothing trades: orders wait for the auction
    s = seller.order(bare_asset, "sell", 10, price=10_500).json()["order"]
    b1 = buyer1.order(bare_asset, "buy", 6, price=10_700).json()["order"]
    b2 = buyer2.order(bare_asset, "buy", 3).json()["order"]
    assert {o["status"] for o in (s, b1, b2)} == {"scheduled"} and s["status_reason"] == krx.VI_REASON
    assert db.execute(select(Trade).where(Trade.asset_id == bare_asset, Trade.seller_id == sid)).first() is None
    # the auction: 9 shares cross at one price (10,500 and 10,700 both trade 9; 10,700 is nearer the last price)
    a = lock_asset(db, bare_asset)
    out = krx.end_vi(db, a, a.vi_until + timedelta(seconds=1))
    db.commit()
    assert out["price"] == 10_700 and out["volume"] == 9
    prices = db.execute(select(Trade.execution_price).where(Trade.asset_id == bare_asset, Trade.seller_id == sid)).scalars().all()
    assert set(prices) == {10_700} and sum(1 for _ in prices) >= 1
    db.expire_all()
    assert db.get(Order, b1["id"]).status == "filled" and db.get(Order, b2["id"]).status == "filled"
    left = db.get(Order, s["id"])
    assert left.status == "partially_filled" and left.remaining_quantity == 1  # continues on the book
    a = db.get(MarketAsset, bare_asset)
    assert a.vi_until is None and a.vi_base == 10_700 and a.current_price == 10_700
    # back to continuous trading
    assert buyer1.order(bare_asset, "buy", 1, price=10_500).json()["order"]["status"] == "filled"
    assert reconciliation.reconcile(db).status == "ok"


def test_heartbeat_waits_during_vi_then_settles(db, asset):
    import random as _r

    from app.services import bots

    bots.ensure_bots(db)
    a = lock_asset(db, asset)
    now = utcnow()
    a.vi_until, a.vi_kind, a.vi_last_at = now + timedelta(seconds=60), "dynamic", now
    db.commit()
    n = db.execute(select(Trade.id).where(Trade.asset_id == asset)).all()
    assert bots.tape_tick(db, [asset], now=now, rng=_r.Random(1))["orders"] == 0
    assert len(db.execute(select(Trade.id).where(Trade.asset_id == asset)).all()) == len(n)
    assert krx.settle_due(db, now + timedelta(seconds=61)) == 1
    db.expire_all()
    assert db.get(MarketAsset, asset).vi_until is None
    assert reconciliation.reconcile(db).status == "ok"


def test_clearing_price_prefers_volume_then_balance():
    class O:
        def __init__(self, side, q, p, i):
            self.side, self.remaining_quantity, self.limit_price, self.protect_price, self.id = side, q, p, None, i

    buys = [O("buy", 5, 1_020, 1), O("buy", 5, 1_000, 2)]
    sells = [O("sell", 4, 990, 3), O("sell", 6, 1_010, 4)]
    assert krx.clearing_price(buys, sells, 1_000) == (1_010, 5)
    assert krx.clearing_price([O("buy", 1, 900, 1)], [O("sell", 1, 1_000, 2)], 950)[1] == 0


def test_sharp_move_on_the_heartbeat_starts_a_dynamic_vi(db, asset):
    import random as _r
    from collections import deque

    from app.services import bots

    bots.ensure_bots(db)
    db.commit()
    now = utcnow()
    price = db.get(MarketAsset, asset).current_price
    bots._hist[asset] = deque([(now - timedelta(seconds=50), price * 96 // 100)], maxlen=400)  # 4% lower a minute ago
    for i in range(10):
        bots.tape_tick(db, [asset], now=now + timedelta(seconds=i), rng=_r.Random(i))
        db.expire_all()
        if db.get(MarketAsset, asset).vi_until is not None:
            break
    a = db.get(MarketAsset, asset)
    assert a.vi_until is not None and a.vi_kind == "dynamic"
    assert reconciliation.reconcile(db).status == "ok"
