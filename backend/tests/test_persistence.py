"""Required test 17: trades and ledger survive a database restart.

Set YEX_TEST_DB_RESTART_CMD (e.g. "service postgresql restart" or
"docker compose restart db") to perform a real server restart. Without it, the test
terminates every server backend and drops the connection pool, which proves data is
committed to the server rather than cached in the application."""

import os
import subprocess
import time

from sqlalchemy import create_engine, func, select, text

from app.config import get_settings
from app.db import reset_engine, session_factory
from app.models import Account, LedgerEntry, Trade


def _restart():
    cmd = os.environ.get("YEX_TEST_DB_RESTART_CMD")
    reset_engine()
    if cmd:
        subprocess.run(cmd, shell=True, check=True, capture_output=True)
    else:
        eng = create_engine(get_settings().database_url)
        with eng.connect() as c:
            c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                           "WHERE datname = current_database() AND pid <> pg_backend_pid()"))
        eng.dispose()
    for _ in range(60):
        try:
            eng = create_engine(get_settings().database_url)
            with eng.connect() as c:
                c.execute(text("SELECT 1"))
            eng.dispose()
            return bool(cmd)
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("database did not come back")


def test_17_trades_survive_restart(make_user, asset, db):
    u = make_user()
    r = u.order(asset, "buy", 9).json()
    trade_ids = [f["id"] for f in r["fills"]]
    assert trade_ids
    before = {
        "trades": db.execute(select(func.count(Trade.id))).scalar_one(),
        "ledger": db.execute(select(func.count(LedgerEntry.id))).scalar_one(),
        "cash": db.get(Account, u.id).cash_balance,
    }
    db.close()
    real = _restart()
    s = session_factory()()
    after = {
        "trades": s.execute(select(func.count(Trade.id))).scalar_one(),
        "ledger": s.execute(select(func.count(LedgerEntry.id))).scalar_one(),
        "cash": s.get(Account, u.id).cash_balance,
    }
    assert after == before, (real, before, after)
    assert {t.id for t in s.execute(select(Trade).where(Trade.id.in_(trade_ids))).scalars()} == set(trade_ids)
    s.close()
    # The API keeps working against the restarted server.
    assert u.get("/api/trades").json()[0]["id"] == trade_ids[-1]
