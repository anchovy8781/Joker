"""Daily ledger ↔ balance reconciliation and economy invariants."""

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Account, LedgerEntry, ReconciliationRun, RiskEvent
from app.services.audit import write_audit


def reconcile(db: Session) -> ReconciliationRun:
    mismatches: list[dict] = []

    # 1) cash_balance == SUM(ledger) for every account
    rows = db.execute(
        select(Account.user_id, Account.cash_balance, func.coalesce(func.sum(LedgerEntry.amount), 0))
        .outerjoin(LedgerEntry, LedgerEntry.user_id == Account.user_id)
        .group_by(Account.user_id, Account.cash_balance)
    ).all()
    for uid, balance, ledger_sum in rows:
        if balance != ledger_sum:
            mismatches.append({"check": "cash_vs_ledger", "user_id": uid, "balance": balance,
                               "ledger_sum": int(ledger_sum)})

    # 2) reserved_cash == SUM(reserved_cash of resting buy orders)
    for uid, reserved, expected in db.execute(text(
        "SELECT a.user_id, a.reserved_cash, COALESCE(SUM(o.reserved_cash), 0) "
        "FROM accounts a LEFT JOIN orders o ON o.user_id = a.user_id AND o.side = 'buy' "
        "AND o.status IN ('open','partially_filled') GROUP BY a.user_id, a.reserved_cash"
    )).all():
        if reserved != expected:
            mismatches.append({"check": "reserved_cash", "user_id": uid, "reserved": reserved,
                               "open_orders": int(expected)})

    # 3) reserved shares == remaining quantity of resting sell orders
    for uid, aid, reserved, expected in db.execute(text(
        "SELECT p.user_id, p.asset_id, p.reserved_quantity, COALESCE(SUM(o.remaining_quantity), 0) "
        "FROM positions p LEFT JOIN orders o ON o.user_id = p.user_id AND o.asset_id = p.asset_id "
        "AND o.side = 'sell' AND o.status IN ('open','partially_filled') "
        "GROUP BY p.user_id, p.asset_id, p.reserved_quantity"
    )).all():
        if reserved != expected:
            mismatches.append({"check": "reserved_shares", "user_id": uid, "asset_id": aid,
                               "reserved": reserved, "open_orders": int(expected)})

    # 4) share conservation: holdings across all accounts == shares_outstanding
    for aid, outstanding, held in db.execute(text(
        "SELECT m.id, m.shares_outstanding, COALESCE(SUM(p.quantity), 0) FROM market_assets m "
        "LEFT JOIN positions p ON p.asset_id = m.id GROUP BY m.id, m.shares_outstanding"
    )).all():
        if outstanding != held:
            mismatches.append({"check": "share_conservation", "asset_id": aid,
                               "outstanding": outstanding, "held": int(held)})

    # 5) each non-system position == net executed quantity
    for uid, aid, qty, net in db.execute(text(
        "SELECT p.user_id, p.asset_id, p.quantity, "
        "COALESCE((SELECT SUM(quantity) FROM trades t WHERE t.buyer_id = p.user_id AND t.asset_id = p.asset_id), 0) - "
        "COALESCE((SELECT SUM(quantity) FROM trades t WHERE t.seller_id = p.user_id AND t.asset_id = p.asset_id), 0) "
        "FROM positions p JOIN users u ON u.id = p.user_id WHERE u.role <> 'system'"
    )).all():
        if qty != net:
            mismatches.append({"check": "position_vs_trades", "user_id": uid, "asset_id": aid,
                               "quantity": qty, "net_trades": int(net)})

    # 6) trade ledger consistency: buy + sell rows plus one row per non-zero fee,
    #    summing to -(fees); a waived fee must really be zero.
    bad_trades = db.execute(text(
        "SELECT t.id FROM trades t LEFT JOIN ledger_entries l "
        "ON l.reference_type = 'trade' AND l.reference_id = t.id::text "
        "GROUP BY t.id, t.buyer_fee, t.seller_fee, t.seller_fee_waived "
        "HAVING COUNT(l.id) <> 2 + (t.buyer_fee > 0)::int + (t.seller_fee > 0)::int "
        "OR COALESCE(SUM(l.amount), 0) <> -(t.buyer_fee + t.seller_fee) "
        "OR (t.seller_fee_waived AND t.seller_fee <> 0)"
    )).scalars().all()
    for tid in bad_trades:
        mismatches.append({"check": "trade_ledger", "trade_id": tid})

    # Economy totals: credits only enter via grants/adjustments and leave via fees.
    totals = {r[0]: int(r[1]) for r in db.execute(
        select(LedgerEntry.entry_type, func.sum(LedgerEntry.amount)).group_by(LedgerEntry.entry_type)
    ).all()}
    total_cash = int(db.execute(select(func.coalesce(func.sum(Account.cash_balance), 0))).scalar_one())
    expected_cash = sum(totals.values())
    if total_cash != expected_cash:
        mismatches.append({"check": "economy_total", "total_cash": total_cash, "expected": expected_cash})
    if totals.get("trade_buy", 0) + totals.get("trade_sell", 0) != 0:
        mismatches.append({"check": "trade_cash_zero_sum", "buy": totals.get("trade_buy", 0),
                           "sell": totals.get("trade_sell", 0)})

    run = ReconciliationRun(
        status="ok" if not mismatches else "mismatch",
        checked_accounts=len(rows),
        mismatches_json=mismatches[:500],
        totals_json={"by_entry_type": totals, "total_cash": total_cash,
                     "fees_burned": -totals.get("trade_fee", 0)},
    )
    db.add(run)
    db.flush()
    if mismatches:
        db.add(RiskEvent(event_type="reconciliation_mismatch", severity="critical", score=0,
                         reason=f"{len(mismatches)} reconciliation mismatches (run {run.id})",
                         evidence_json={"run_id": run.id, "sample": mismatches[:10]}))
    write_audit(db, "economy.reconcile", target_type="reconciliation_run", target_id=run.id,
                metadata={"status": run.status, "mismatches": len(mismatches)})
    return run
