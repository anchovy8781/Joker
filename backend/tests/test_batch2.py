"""Trading sessions & reservations, auto orders, donations, listing request window,
popularity (1-50) and price engine v2."""

import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.models import Account, AutoOrder, Donation, LedgerEntry, MarketAsset, Order, PopularityHistory, Trade, User
from app.security import utcnow
from app.services import collector, market_hours, orders, popularity, pricing
from app.services.reconciliation import reconcile
from tests.conftest import give_shares, list_demo_asset

KST = ZoneInfo("Asia/Seoul")


def kst(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


def acct(db, uid):
    db.expire_all()
    return db.get(Account, uid)


# ---------------------------------------------------------------- trading sessions
@pytest.mark.parametrize("when,expected", [
    (kst(2026, 10, 19, 16, 59), False),  # Monday before 17:00
    (kst(2026, 10, 19, 17, 0), True),
    (kst(2026, 10, 19, 23, 9), True),
    (kst(2026, 10, 19, 23, 10), False),
    (kst(2026, 10, 20, 16, 0), True),    # Tuesday additional time
    (kst(2026, 10, 21, 15, 59), False),  # Wednesday
    (kst(2026, 10, 21, 16, 0), True),
    (kst(2026, 10, 22, 16, 30), False),  # Thursday: no additional time
    (kst(2026, 10, 23, 16, 0), True),    # Friday additional time
    (kst(2026, 10, 24, 12, 59), False),  # Saturday
    (kst(2026, 10, 24, 13, 0), True),
    (kst(2026, 10, 25, 21, 59), True),   # Sunday
    (kst(2026, 10, 25, 22, 0), False),
])
def test_trading_sessions(monkeypatch, when, expected):
    monkeypatch.setattr(get_settings(), "market_hours_enabled", True)
    assert market_hours.is_open(when) is expected


def test_next_open_is_reported(monkeypatch):
    monkeypatch.setattr(get_settings(), "market_hours_enabled", True)
    st = market_hours.status(kst(2026, 10, 19, 23, 30))  # Monday after close
    assert st.next_open == kst(2026, 10, 20, 16, 0)  # Tuesday 16:00


@pytest.fixture
def closed(monkeypatch):
    state = {"open": False}
    monkeypatch.setattr(orders.market_hours, "is_open", lambda now: state["open"])
    return state


def test_orders_outside_hours_become_reservations(make_user, asset, db, closed):
    u = make_user()
    r = u.order(asset, "buy", 3, price=10_100)
    assert r.status_code == 201
    o = r.json()["order"]
    assert o["status"] == "scheduled" and r.json()["fills"] == []  # MM asks exist, but no matching yet
    assert acct(db, u.id).reserved_cash == o["reserved_cash"] > 0
    m = u.order(asset, "buy", 2).json()["order"]  # market reservation
    assert m["status"] == "scheduled"
    assert db.execute(select(func.count(Trade.id))).scalar_one() == 0
    assert any(n["kind"] == "order_scheduled" for n in u.get("/api/notifications").json())
    assert reconcile(db).status == "ok"
    # Cancel a reservation -> funds back
    assert u.post(f"/api/orders/{m['id']}/cancel").status_code == 200
    assert acct(db, u.id).reserved_cash == o["reserved_cash"]


def test_reservations_activate_in_submission_order_at_open(make_user, bare_asset, db, closed):
    seller, b1, b2 = make_user(), make_user(), make_user()
    closed["open"] = True
    give_shares(db, seller.id, bare_asset, 5)
    seller.order(bare_asset, "sell", 5, price=10_000)
    closed["open"] = False
    first = b1.order(bare_asset, "buy", 4, price=10_000).json()["order"]["id"]
    second = b2.order(bare_asset, "buy", 4, price=10_000).json()["order"]["id"]
    closed["open"] = True
    for oid in sorted([second, first]):
        orders.activate_scheduled(db, oid)
        db.commit()
    db.expire_all()
    assert db.get(Order, first).status == "filled"
    o2 = db.get(Order, second)
    assert o2.status == "partially_filled" and o2.remaining_quantity == 3  # time priority kept
    assert reconcile(db).status == "ok"


def test_market_reservation_never_exceeds_reserved_price(make_user, bare_asset, db, closed):
    seller, buyer = make_user(), make_user()
    closed["open"] = True
    give_shares(db, seller.id, bare_asset, 10)
    closed["open"] = False
    o = buyer.order(bare_asset, "buy", 3).json()["order"]
    a = db.get(MarketAsset, bare_asset)
    a.reference_price = 11_000  # reference rises before the open (daily update)
    db.commit()
    closed["open"] = True
    seller.order(bare_asset, "sell", 10, price=10_600)  # above the reservation's 10,500 cap
    orders.activate_scheduled(db, o["id"])
    db.commit()
    db.expire_all()
    res = db.get(Order, o["id"])
    assert res.status == "canceled" and res.status_reason == "NO_LIQUIDITY"
    assert acct(db, buyer.id).reserved_cash == 0 and acct(db, buyer.id).cash_balance == 1_000_000


def test_limit_reservation_out_of_band_at_open_is_canceled(make_user, bare_asset, db, closed):
    u = make_user()
    o = u.order(bare_asset, "buy", 1, price=9_400).json()["order"]
    a = db.get(MarketAsset, bare_asset)
    a.reference_price = 11_000  # 9,400 is now outside the ±7% low-liquidity band
    db.commit()
    closed["open"] = True
    orders.activate_scheduled(db, o["id"])
    db.commit()
    db.expire_all()
    assert db.get(Order, o["id"]).status_reason == "OUT_OF_BAND_AT_OPEN"
    assert acct(db, u.id).reserved_cash == 0


def test_market_hours_endpoint(make_user):
    body = make_user().get("/api/market/hours").json()
    assert body["timezone"] == "Asia/Seoul" and len(body["schedule"]) == 7
    assert body["schedule"][1] == {"weekday": "화", "open": "16:00", "close": "23:10"}
    assert body["schedule"][5] == {"weekday": "토", "open": "13:00", "close": "22:00"}


# ---------------------------------------------------------------- auto orders
def test_auto_sell_fires_when_price_reaches_trigger(make_user, asset, db):
    u = make_user()
    give_shares(db, u.id, asset, 10)
    r = u.post("/api/auto-orders", json={"asset_id": asset, "side": "sell", "trigger_condition": "gte",
                                         "trigger_price": 10_200, "order_type": "limit", "limit_price": 10_200,
                                         "quantity": 4})
    assert r.status_code == 201 and r.json()["status"] == "active"
    a = db.get(MarketAsset, asset)
    a.current_price = 10_250
    db.commit()
    from app.services import auto_orders

    assert auto_orders.evaluate(db, asset)["fired"] == 1
    db.commit()
    ao = u.get("/api/auto-orders").json()[0]
    assert ao["status"] == "triggered" and ao["order_id"]
    placed = db.get(Order, ao["order_id"])
    assert placed.side == "sell" and placed.limit_price == 10_200 and placed.idempotency_key == f"auto:{ao['id']}"
    assert any(n["kind"] == "auto_order_triggered" for n in u.get("/api/notifications").json())


def test_auto_buy_fires_after_a_trade_moves_the_price(make_user, bare_asset, db):
    watcher, seller, buyer = make_user(), make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 10)
    seller.order(bare_asset, "sell", 10, price=9_800)
    watcher.post("/api/auto-orders", json={"asset_id": bare_asset, "side": "buy", "trigger_condition": "lte",
                                           "trigger_price": 9_850, "order_type": "market", "quantity": 2})
    assert watcher.get("/api/auto-orders").json()[0]["status"] == "active"
    buyer.order(bare_asset, "buy", 1, price=9_800)  # last price 9,800 <= 9,850 -> trigger
    ao = watcher.get("/api/auto-orders").json()[0]
    assert ao["status"] == "triggered"
    assert db.get(Order, ao["order_id"]).status == "filled"


def test_auto_order_fails_cleanly_without_funds(make_user, asset, db):
    u = make_user()
    u.post("/api/auto-orders", json={"asset_id": asset, "side": "sell", "trigger_condition": "gte",
                                     "trigger_price": 1, "order_type": "market", "quantity": 5})
    ao = u.get("/api/auto-orders").json()[0]  # trigger already met at creation
    assert ao["status"] == "failed" and ao["failure_reason"].startswith("INSUFFICIENT_SHARES")


def test_auto_order_validation_privacy_and_cancel(make_user, asset, monkeypatch):
    u, other = make_user(), make_user()
    bad = u.post("/api/auto-orders", json={"asset_id": asset, "side": "buy", "trigger_condition": "lte",
                                           "trigger_price": 9_000, "order_type": "limit", "quantity": 1})
    assert bad.status_code == 422
    ok = u.post("/api/auto-orders", json={"asset_id": asset, "side": "buy", "trigger_condition": "lte",
                                          "trigger_price": 5_000, "order_type": "market", "quantity": 1}).json()
    assert other.post(f"/api/auto-orders/{ok['id']}/cancel").status_code == 404
    assert other.get("/api/auto-orders").json() == []
    assert u.post(f"/api/auto-orders/{ok['id']}/cancel").json()["status"] == "canceled"
    monkeypatch.setattr(get_settings(), "auto_orders_max_active", 1)
    u.post("/api/auto-orders", json={"asset_id": asset, "side": "buy", "trigger_condition": "lte",
                                     "trigger_price": 5_000, "order_type": "market", "quantity": 1})
    r = u.post("/api/auto-orders", json={"asset_id": asset, "side": "buy", "trigger_condition": "lte",
                                         "trigger_price": 5_000, "order_type": "market", "quantity": 1})
    assert r.json()["error"]["code"] == "TOO_MANY_AUTO_ORDERS"


# ---------------------------------------------------------------- donations
def _eligible(db, u, asset, network):
    """Old enough, enough trades, and on its own network."""
    for _ in range(5):
        u.order(asset, "buy", 1)
    db.execute(text("UPDATE users SET created_at = now() - interval '5 days', signup_ip_hash = :n WHERE id = :u"),
               {"n": network, "u": u.id})
    db.execute(text("UPDATE sessions SET ip_hash = :n WHERE user_id = :u"), {"n": network, "u": u.id})
    db.commit()


def test_donation_transfers_credits_zero_sum(make_user, asset, db):
    a, b = make_user(), make_user()
    _eligible(db, a, asset, "net-a")
    db.execute(text("UPDATE users SET signup_ip_hash='net-b' WHERE id=:u"), {"u": b.id})
    db.execute(text("UPDATE sessions SET ip_hash='net-b' WHERE user_id=:u"), {"u": b.id})
    db.commit()
    a_before, b_before = acct(db, a.id).cash_balance, acct(db, b.id).cash_balance
    r = a.post("/api/donations", json={"recipient": b.username, "amount": 120_000, "message": "응원해요!"})
    assert r.status_code == 201, r.text
    assert acct(db, a.id).cash_balance == a_before - 120_000
    assert acct(db, b.id).cash_balance == b_before + 120_000
    box = b.get("/api/donations").json()
    assert box["box"]["received_today"] == 120_000 and box["box"]["receive_remaining"] == 380_000
    assert box["history"][0]["sender"] == a.username and box["history"][0]["message"] == "응원해요!"
    assert any(n["kind"] == "donation_received" for n in b.get("/api/notifications").json())
    run = reconcile(db)
    assert run.status == "ok"
    t = run.totals_json["by_entry_type"]
    assert t["donation_sent"] == -120_000 and t["donation_received"] == 120_000


def test_donation_box_daily_limit_500k(make_user, asset, db):
    s1, s2, r = make_user(), make_user(), make_user()
    _eligible(db, s1, asset, "n1")
    _eligible(db, s2, asset, "n2")
    db.execute(text("UPDATE users SET signup_ip_hash='n3' WHERE id=:u"), {"u": r.id})
    db.execute(text("UPDATE sessions SET ip_hash='n3' WHERE user_id=:u"), {"u": r.id})
    db.commit()
    assert s1.post("/api/donations", json={"recipient": r.username, "amount": 300_000}).status_code == 201
    full = s2.post("/api/donations", json={"recipient": r.username, "amount": 250_000})
    assert full.status_code == 409 and full.json()["error"]["code"] == "DONATION_BOX_FULL"
    assert s2.post("/api/donations", json={"recipient": r.username, "amount": 200_000}).status_code == 201
    assert r.get("/api/donations").json()["box"]["full"] is True
    over = s1.post("/api/donations", json={"recipient": r.username, "amount": 600_000})
    assert over.status_code == 422  # a single gift can never exceed 500,000


def test_donation_box_limit_holds_under_concurrency(make_user, asset, db):
    senders = [make_user() for _ in range(4)]
    for i, s in enumerate(senders):
        _eligible(db, s, asset, f"cn{i}")
    r = make_user()
    db.execute(text("UPDATE users SET signup_ip_hash='crecv' WHERE id=:u"), {"u": r.id})
    db.execute(text("UPDATE sessions SET ip_hash='crecv' WHERE user_id=:u"), {"u": r.id})
    db.commit()
    barrier = threading.Barrier(4)
    codes = []

    def go(s):
        barrier.wait()
        codes.append(s.post("/api/donations", json={"recipient": r.username, "amount": 200_000}).status_code)

    ts = [threading.Thread(target=go, args=(s,)) for s in senders]
    [t.start() for t in ts]
    [t.join() for t in ts]
    received = db.execute(select(func.sum(Donation.amount)).where(Donation.recipient_id == r.id)).scalar_one()
    assert received <= 500_000 and codes.count(201) == 2, codes


def test_donation_anti_abuse_rules(make_user, asset, db):
    a, b = make_user(), make_user()
    r = a.post("/api/donations", json={"recipient": b.username, "amount": 1_000})
    assert r.json()["error"]["code"] == "ACCOUNT_TOO_NEW"  # brand-new accounts can't funnel grants
    db.execute(text("UPDATE users SET created_at = now() - interval '5 days' WHERE id = :u"), {"u": a.id})
    db.commit()
    assert a.post("/api/donations", json={"recipient": b.username, "amount": 1_000}).json()["error"]["code"] == "NOT_ENOUGH_ACTIVITY"
    for _ in range(5):
        a.order(asset, "buy", 1)
    # a and b were created from the same test network -> linked
    linked = a.post("/api/donations", json={"recipient": b.username, "amount": 1_000})
    assert linked.status_code == 403 and linked.json()["error"]["code"] == "LINKED_ACCOUNTS"
    assert a.post("/api/donations", json={"recipient": a.username, "amount": 1_000}).json()["error"]["code"] == "SELF_DONATION"
    assert a.post("/api/donations", json={"recipient": "nobody_here", "amount": 1_000}).status_code == 404
    assert db.execute(select(func.count(Donation.id))).scalar_one() == 0


# ---------------------------------------------------------------- listing request window
def test_listing_requests_open_at_19_kst(make_user, db, monkeypatch):
    from app.services import listing_requests

    monkeypatch.setattr(get_settings(), "listing_request_open_hour_kst", 19)
    u = make_user()
    monkeypatch.setattr(listing_requests, "utcnow", lambda: kst(2026, 10, 19, 18, 59))
    r = u.post("/api/listing-requests", json={"channel": "UC" + "w" * 22})
    assert r.status_code == 409 and r.json()["error"]["code"] == "LISTING_REQUESTS_CLOSED"
    assert r.json()["error"]["message"] == "상장 요청은 매일 오후 7시부터 가능합니다"
    monkeypatch.setattr(listing_requests, "utcnow", lambda: kst(2026, 10, 19, 19, 0))
    assert u.post("/api/listing-requests", json={"channel": "UC" + "w" * 22}).status_code == 201


def test_listing_requests_global_daily_cap(make_user, make_admin, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "listing_requests_per_day", 2)
    admin = make_admin()
    users = [make_user() for _ in range(3)]
    assert users[0].post("/api/listing-requests", json={"channel": "UC" + "1" * 22}).status_code == 201
    assert users[1].post("/api/listing-requests", json={"channel": "UC" + "2" * 22}).status_code == 201
    r = users[2].post("/api/listing-requests", json={"channel": "UC" + "3" * 22})
    assert r.status_code == 409 and r.json()["error"]["code"] == "LISTING_REQUEST_LIMIT_REACHED"
    assert r.json()["error"]["message"] == "오늘의 상장 요청 최대한도에 도달했습니다"
    st = users[2].get("/api/listing-requests/status").json()
    assert st["limit_reached"] is True and st["remaining"] == 0 and st["limit"] == 2
    # Remote approval: admins are notified of every request
    notes = admin.get("/api/notifications").json()
    assert [n["kind"] for n in notes].count("listing_request") == 2


def test_listing_request_cap_holds_under_concurrency(make_user, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "listing_requests_per_day", 3)
    users = [make_user() for _ in range(8)]
    barrier = threading.Barrier(8)
    codes = []

    def go(i, u):
        barrier.wait()
        codes.append(u.post("/api/listing-requests", json={"channel": "UC" + chr(97 + i) * 22}).status_code)

    ts = [threading.Thread(target=go, args=(i, u)) for i, u in enumerate(users)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert codes.count(201) == 3, codes


# ---------------------------------------------------------------- popularity
def test_popularity_score_bounds_and_missing_data():
    assert popularity.score_components(1, 1, 1, 1, 1, 1)[0] == 50
    assert popularity.score_components(0, 0, 0, 0, 0, 0)[0] == 1
    score, detail = popularity.score_components(1, 1, None, None, 1, 1)  # likes hidden
    assert score == 50 and "engagement" not in detail["weights"]
    assert abs(sum(detail["weights"].values()) - 1) < 1e-3


def test_popularity_computed_on_collection_with_likes(db, make_user):
    a1 = list_demo_asset(db, symbol="DPOPA")
    a2 = list_demo_asset(db, symbol="DPOPB")
    make_user().order(a1, "buy", 5)
    res = collector.collect_all(db)
    db.commit()
    assert set(res["popularity"]) == {"DPOPA", "DPOPB"}
    for aid in (a1, a2):
        a = db.get(MarketAsset, aid)
        assert 1 <= a.popularity_score <= 50
    hist = db.execute(select(PopularityHistory).where(PopularityHistory.asset_id == a1)).scalar_one()
    assert hist.components_json["components"]["engagement"] is not None  # likes were used
    assert hist.components_json["components"]["market"] == 1.0  # only traded asset
    detail = make_user().get(f"/api/assets/{a1}").json()
    assert detail["popularity_score"] == db.get(MarketAsset, a1).popularity_score


def test_youtube_engagement_parsing():
    import httpx

    from app.services.youtube import YouTubeDataAPIProvider

    def handler(req):
        if req.url.path.endswith("/playlistItems"):
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": "v1"}},
                                                       {"contentDetails": {"videoId": "v2"}}]})
        return httpx.Response(200, json={"items": [
            {"id": "v1", "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "5"}},
            {"id": "v2", "statistics": {"viewCount": "500"}},  # likes hidden on this video
        ]})

    api = YouTubeDataAPIProvider("k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    eng = api.fetch_engagement({"UCx": "UUx"})["UCx"]
    assert (eng.videos, eng.views, eng.likes, eng.comments) == (2, 1500, 50, 5)


# ---------------------------------------------------------------- price engine v2
def test_engagement_and_popularity_move_fundamentals():
    base = dict(has_7d=True, has_28d=True, hidden_subs=False, sub_g7=0.0, sub_g28w=0.0, view_g7=0.0,
                view_g28w=0.0, view_g7_prev=0.0, confidence=1.0)
    flat, _ = pricing.fundamental_return(pricing.Features(**base), 0.0, 0.0)
    up, info = pricing.fundamental_return(pricing.Features(**base, like_rate=0.05, like_rate_prev=0.03),
                                          0.0, 0.0, pop_now=40, pop_prev=30)
    down, _ = pricing.fundamental_return(pricing.Features(**base, like_rate=0.02, like_rate_prev=0.05),
                                         0.0, 0.0, pop_now=20, pop_prev=35)
    assert flat == 0 and up > 0 > down
    assert abs(up) <= 0.04 and abs(down) <= 0.04
    assert info["weights"] == {"subscribers": 0.3, "views": 0.3, "trend": 0.15, "engagement": 0.15, "popularity": 0.1}


def test_flow_decay_and_liquidity(make_user, bare_asset, db):
    seller = make_user()
    give_shares(db, seller.id, bare_asset, 20)  # buy-aggressor trade
    now = utcnow()
    r_now, i_now = pricing.flow_return(db, bare_asset, now - timedelta(days=1), now)
    _, thin = pricing.flow_return(db, bare_asset, now - timedelta(days=1), now, market_cap=10**12)
    _, deep = pricing.flow_return(db, bare_asset, now - timedelta(days=1), now, market_cap=10**6)
    assert thin["liquidity_confidence"] < 0.1 and deep["liquidity_confidence"] == 1.0
    assert r_now > 0 and i_now["imbalance"] > 0


def test_flow_decay_weights_recent_trades_more(make_user, bare_asset, db):
    seller = make_user()
    give_shares(db, seller.id, bare_asset, 10)  # creates one real trade we can copy
    t = db.execute(select(Trade)).scalar_one()
    now = utcnow()
    copy = text(
        "INSERT INTO trades (trade_uid, asset_id, buy_order_id, sell_order_id, buyer_id, seller_id, quantity, "
        "execution_price, buyer_fee, seller_fee, taker_side, seller_realized_pnl, seller_fee_waived, executed_at) "
        "SELECT gen_random_uuid(), asset_id, buy_order_id, sell_order_id, buyer_id, seller_id, quantity, "
        "execution_price, buyer_fee, seller_fee, :side, 0, false, :at FROM trades WHERE id = :id")
    db.execute(copy, {"side": "buy", "at": now - timedelta(hours=12), "id": t.id})  # old buying
    # Recent selling by the user (buyer/seller swapped so the aggressor is not the market maker).
    db.execute(text(
        "INSERT INTO trades (trade_uid, asset_id, buy_order_id, sell_order_id, buyer_id, seller_id, quantity, "
        "execution_price, buyer_fee, seller_fee, taker_side, seller_realized_pnl, seller_fee_waived, executed_at) "
        "SELECT gen_random_uuid(), asset_id, sell_order_id, buy_order_id, seller_id, buyer_id, quantity, "
        "execution_price, buyer_fee, seller_fee, 'sell', 0, false, :at FROM trades WHERE id = :id"),
        {"at": now - timedelta(minutes=5), "id": t.id})
    db.commit()
    # Old buy vs recent sell of equal size: raw imbalance 0, decayed imbalance clearly negative.
    _, info = pricing.flow_return(db, bare_asset, now - timedelta(hours=13), now - timedelta(minutes=2))
    assert info["buy_notional"] == info["sell_notional"]
    assert info["imbalance"] < -0.5  # 12h old = two half-lives -> weight 0.25 vs ~1.0


def test_daily_update_records_v2_inputs(db, make_user):
    aids = [list_demo_asset(db, symbol=f"DV{i}") for i in range(3)]
    collector.collect_all(db)
    db.commit()
    summary = pricing.run_daily_update(db, utcnow().date())
    db.commit()
    assert summary["applied"] == 3
    from app.models import PriceUpdate

    upd = db.execute(select(PriceUpdate).where(PriceUpdate.asset_id == aids[0])).scalar_one()
    assert upd.formula_version == "hybrid-v2"
    assert upd.inputs_json["popularity"]["now"] is not None
    assert "liquidity_confidence" in upd.inputs_json["flow"]
    a = db.get(MarketAsset, aids[0])
    assert abs(a.reference_price / a.previous_price - 1) <= 0.08
