"""Required test 10 + immutable ledger, admin adjustments and economy invariants."""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.models import Account, AuditLog, LedgerEntry
from app.services.reconciliation import reconcile
from tests.conftest import give_shares


def test_10_ledger_sum_equals_balance(make_user, asset, db):
    users = [make_user() for _ in range(3)]
    for u in users:
        u.order(asset, "buy", 7)
    give_shares(db, users[0].id, asset, 5)
    users[0].order(asset, "sell", 4, price=10_000)
    users[1].order(asset, "buy", 4, price=10_000)
    db.expire_all()
    for u in users:
        bal = db.get(Account, u.id).cash_balance
        s = db.execute(select(func.sum(LedgerEntry.amount)).where(LedgerEntry.user_id == u.id)).scalar_one()
        assert bal == s
        latest = db.execute(select(LedgerEntry).where(LedgerEntry.user_id == u.id)
                            .order_by(LedgerEntry.id.desc()).limit(1)).scalar_one()
        assert latest.balance_after == bal
    run = reconcile(db)
    assert run.status == "ok", run.mismatches_json
    totals = run.totals_json
    # Credits only enter via grants and only leave via fees; trades are zero-sum.
    assert totals["by_entry_type"]["trade_buy"] + totals["by_entry_type"]["trade_sell"] == 0
    assert totals["total_cash"] == totals["by_entry_type"]["signup_grant"] + totals["by_entry_type"]["trade_fee"]
    assert totals["fees_burned"] > 0


def test_reconciliation_detects_tampering(make_user, db):
    u = make_user()
    db.execute(text("UPDATE accounts SET cash_balance = cash_balance + 5 WHERE user_id = :u"), {"u": u.id})
    db.commit()
    run = reconcile(db)
    assert run.status == "mismatch"
    assert any(m["check"] == "cash_vs_ledger" and m["user_id"] == u.id for m in run.mismatches_json)


@pytest.mark.parametrize("stmt", [
    "UPDATE ledger_entries SET amount = amount + 1",
    "DELETE FROM ledger_entries",
    "UPDATE trades SET quantity = quantity + 1",
    "DELETE FROM trades",
    "UPDATE audit_logs SET action = 'x'",
    "DELETE FROM audit_logs",
])
def test_ledger_trades_and_audit_are_append_only(make_user, asset, db, stmt):
    make_user().order(asset, "buy", 1)
    with pytest.raises(DBAPIError) as exc:
        db.execute(text(stmt))
        db.flush()
    assert "append-only" in str(exc.value)
    db.rollback()


def test_admin_adjustment_requires_reason_and_is_audited(make_admin, make_user, db):
    admin, u = make_admin(), make_user()
    r = admin.post("/api/admin/adjustments", json={"user_id": u.id, "amount": 500, "reason": "short",
                                                    "idempotency_key": "adj-000001"})
    assert r.status_code == 422  # reason too short
    body = {"user_id": u.id, "amount": 500, "reason": "Compensation for incident #42 outage",
            "idempotency_key": "adj-000002"}
    r = admin.post("/api/admin/adjustments", json=body)
    assert r.status_code == 201 and r.json()["balance_after"] == 1_000_500
    assert admin.post("/api/admin/adjustments", json=body).status_code == 409  # no double application
    neg = admin.post("/api/admin/adjustments", json={**body, "amount": -2_000_000, "idempotency_key": "adj-000003"})
    assert neg.status_code == 409 and neg.json()["error"]["code"] == "INSUFFICIENT_FUNDS"
    entry = db.execute(select(LedgerEntry).where(LedgerEntry.entry_type == "admin_adjustment")).scalar_one()
    assert entry.actor_id == admin.id and "incident" in entry.memo
    log = db.execute(select(AuditLog).where(AuditLog.action == "economy.admin_adjustment")).scalar_one()
    assert log.actor_id == admin.id and log.metadata_json["reason"] == body["reason"]
    assert reconcile(db).status == "ok"


def test_no_api_route_can_modify_or_delete_trades_or_ledger(app):
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        assert not ({"PUT", "PATCH", "DELETE"} & methods), path
        if "ledger" in path or "trades" in path:
            assert methods <= {"GET", "HEAD"}, path


def test_share_conservation(make_user, asset, db):
    for _ in range(3):
        make_user().order(asset, "buy", 11)
    held = db.execute(text("SELECT SUM(quantity) FROM positions WHERE asset_id=:a"), {"a": asset}).scalar_one()
    assert held == 1_000_000


def test_cash_never_negative_db_constraint(make_user, db):
    u = make_user()
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE accounts SET cash_balance = -1 WHERE user_id = :u"), {"u": u.id})
        db.flush()
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE accounts SET reserved_cash = cash_balance + 1 WHERE user_id = :u"), {"u": u.id})
    db.rollback()


def test_duplicate_signup_grant_blocked_by_db_index(make_user, db):
    u = make_user()
    with pytest.raises(DBAPIError):
        db.execute(text(
            "INSERT INTO ledger_entries (transaction_id, user_id, entry_type, amount, currency, balance_after) "
            "VALUES (:t, :u, 'signup_grant', 1000000, 'CRD', 2000000)"), {"t": str(uuid.uuid4()), "u": u.id})
    db.rollback()
