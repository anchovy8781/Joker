"""Cash ledger. Every change to accounts.cash_balance goes through post_entry()."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Account, LedgerEntry


def lock_account(db: Session, user_id: int) -> Account:
    db.flush()  # write pending changes first: the locked read below refreshes cached rows
    account = db.execute(
        select(Account).where(Account.user_id == user_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if account is None:
        raise AppError(404, "ACCOUNT_NOT_FOUND", "Account not found")
    return account


def lock_accounts(db: Session, user_ids: set[int]) -> dict[int, Account]:
    """Lock several accounts in ascending user_id order to minimise deadlocks."""
    db.flush()  # write pending changes first: the locked read below refreshes cached rows
    rows = db.execute(
        select(Account)
        .where(Account.user_id.in_(sorted(user_ids)))
        .order_by(Account.user_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalars()
    return {a.user_id: a for a in rows}


def post_entry(
    db: Session,
    account: Account,
    *,
    entry_type: str,
    amount: int,
    transaction_id: uuid.UUID,
    reference_type: str | None = None,
    reference_id: str | int | None = None,
    actor_id: int | None = None,
    memo: str | None = None,
) -> LedgerEntry:
    """Apply a signed amount to a locked account and append the matching ledger row."""
    if amount == 0:
        raise ValueError("ledger amount must be non-zero")
    new_balance = account.cash_balance + amount
    # Callers release any matching reservation before debiting, so reserved funds stay covered.
    if new_balance < 0 or new_balance < account.reserved_cash:
        raise AppError(409, "INSUFFICIENT_FUNDS", "Balance cannot become negative")
    account.cash_balance = new_balance
    account.version += 1
    entry = LedgerEntry(
        transaction_id=transaction_id,
        user_id=account.user_id,
        entry_type=entry_type,
        amount=amount,
        balance_after=new_balance,
        reference_type=reference_type,
        reference_id=str(reference_id) if reference_id is not None else None,
        actor_id=actor_id,
        memo=memo,
    )
    db.add(entry)
    return entry
