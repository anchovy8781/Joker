"""Regression: a cancel that waited for the lock while a fill happened must see the fill
(previously the cached, pre-lock copy of the order was reused -> double release)."""

import threading
import uuid

from sqlalchemy import text

from app.db import run_in_transaction, session_factory
from app.errors import AppError
from app.models import Order, User
from app.services.orders import OrderRequest, cancel_order, lock_asset, place_order
from app.services.reconciliation import reconcile
from tests.conftest import give_shares


def test_cancel_after_concurrent_fill_sees_fresh_state(make_user, bare_asset, db):
    seller, buyer = make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 50)
    order_id = seller.order(bare_asset, "sell", 50, price=10_000).json()["order"]["id"]

    s1 = session_factory()()
    s1.get(Order, order_id)  # cancel's pre-lock read: caches status 'open'
    holder = session_factory()()
    lock_asset(holder, bare_asset)  # hold the asset lock so the cancel must wait

    result = {}

    def do_cancel():
        try:
            result["c"] = run_in_transaction(s1, lambda s: cancel_order(s, s.get(User, seller.id), order_id)).status
        except AppError as e:
            result["c"] = e.code

    t = threading.Thread(target=do_cancel)
    t.start()
    # While the cancel waits, the order is filled and committed by another transaction.
    holder.rollback()
    holder.close()
    s2 = session_factory()()
    run_in_transaction(s2, lambda s: place_order(s, s.get(User, buyer.id),
                                                 OrderRequest(bare_asset, "buy", "limit", 50, 10_000, uuid.uuid4().hex)))
    s2.close()
    t.join(10)
    s1.close()
    assert result["c"] in ("ORDER_NOT_CANCELABLE", "canceled")
    pos = db.execute(text("SELECT quantity, reserved_quantity FROM positions WHERE user_id=:u AND asset_id=:a"),
                     {"u": seller.id, "a": bare_asset}).one()
    assert pos.reserved_quantity >= 0
    run = reconcile(db)
    assert run.status == "ok", (result, run.mismatches_json)
