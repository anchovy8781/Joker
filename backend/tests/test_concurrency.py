"""Required test 5 (+ concurrent idempotency and cancel-vs-fill races), using real
PostgreSQL row locks across parallel sessions and parallel HTTP requests."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.db import run_in_transaction, session_factory
from app.errors import AppError
from app.models import Account, Order, Trade, User
from app.services.orders import OrderRequest, cancel_order, place_order
from app.services.reconciliation import reconcile
from tests.conftest import ApiUser, give_shares


def _place(user_id, req):
    s = session_factory()()
    try:
        r = run_in_transaction(s, lambda x: place_order(x, x.get(User, user_id), req))
        return r.order.status
    except AppError as e:
        return e.code
    finally:
        s.close()


def test_05_concurrent_buys_never_overdraw(make_user, bare_asset, db):
    seller, buyer = make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 90)
    seller.order(bare_asset, "sell", 90, price=10_000)
    # Each order needs ~200k reserved; only 4 of 20 can be funded from 1,000,000.
    reqs = [OrderRequest(bare_asset, "buy", "limit", 20, 10_000, uuid.uuid4().hex) for _ in range(20)]
    barrier = threading.Barrier(20)

    def go(req):
        barrier.wait()
        return _place(buyer.id, req)

    with ThreadPoolExecutor(20) as ex:
        results = list(ex.map(go, reqs))
    assert results.count("filled") == 4, results
    assert results.count("INSUFFICIENT_FUNDS") == 16
    db.expire_all()
    a = db.get(Account, buyer.id)
    assert a.cash_balance >= 0 and a.reserved_cash == 0
    assert a.cash_balance == 1_000_000 - 4 * (200_000 + 400)
    assert reconcile(db).status == "ok"


def test_05b_concurrent_http_orders_balance_stays_non_negative(app, make_user, asset, db):
    buyer = make_user()
    barrier = threading.Barrier(12)

    def go(_):
        barrier.wait()
        return buyer.order(asset, "buy", 40).status_code  # market buys walking the MM ladder

    with ThreadPoolExecutor(12) as ex:
        codes = list(ex.map(go, range(12)))
    assert set(codes) <= {201, 409}, codes
    db.expire_all()
    a = db.get(Account, buyer.id)
    assert a.cash_balance >= 0 and a.reserved_cash == 0
    assert reconcile(db).status == "ok"


def test_06b_concurrent_duplicate_submissions_single_order(make_user, asset, db):
    buyer = make_user()
    key = "same-key-" + uuid.uuid4().hex[:8]
    barrier = threading.Barrier(10)

    def go(_):
        barrier.wait()
        r = buyer.order(asset, "buy", 3, key=key)
        return r.status_code, r.json().get("order", {}).get("id")

    with ThreadPoolExecutor(10) as ex:
        res = list(ex.map(go, range(10)))
    ids = {i for _, i in res if i}
    assert len(ids) == 1, res
    assert sorted(c for c, _ in res).count(201) == 1
    assert db.execute(select(func.count(Order.id)).where(Order.user_id == buyer.id)).scalar_one() == 1
    assert db.execute(select(func.sum(Trade.quantity)).where(Trade.buyer_id == buyer.id)).scalar_one() == 3


def test_cancel_racing_with_fill_is_consistent(make_user, bare_asset, db):
    seller, buyer = make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 50)
    order_id = seller.order(bare_asset, "sell", 50, price=10_000).json()["order"]["id"]
    barrier = threading.Barrier(2)

    def do_cancel():
        barrier.wait()
        s = session_factory()()
        try:
            return run_in_transaction(s, lambda x: cancel_order(x, x.get(User, seller.id), order_id)).status
        except AppError as e:
            return e.code
        finally:
            s.close()

    def do_buy():
        barrier.wait()
        return _place(buyer.id, OrderRequest(bare_asset, "buy", "limit", 50, 10_000, uuid.uuid4().hex))

    with ThreadPoolExecutor(2) as ex:
        fc, fb = ex.submit(do_cancel), ex.submit(do_buy)
        c, b = fc.result(), fb.result()
    # Either the cancel won (buy rests unfilled) or the fill won (cancel refused). Never both.
    assert (c, b) in {("canceled", "open"), ("ORDER_NOT_CANCELABLE", "filled")}, (c, b)
    assert reconcile(db).status == "ok"


def test_many_users_trading_concurrently_reconciles(app, make_user, asset, db):
    users = [make_user() for _ in range(6)]
    for u in users:
        give_shares(db, u.id, asset, 50)

    def trade(u: ApiUser):
        for i in range(6):
            if i % 2:
                u.order(asset, "sell", 5, price=9_990 + i)
            else:
                u.order(asset, "buy", 5, price=10_010 - i)

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(trade, users))
    run = reconcile(db)
    assert run.status == "ok", run.mismatches_json
