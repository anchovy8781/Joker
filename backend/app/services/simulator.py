"""Background market simulator: runs bot rounds every few seconds inside the web process.

Exactly one process runs it: the thread holds a PostgreSQL session advisory lock on a
dedicated connection for as long as it lives; other processes keep retrying quietly.
"""

import logging
import threading
import time

from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine, session_factory

log = logging.getLogger("yex.simulator")
LOCK_KEY = 7_123_456_789
_started = False
_stop = threading.Event()


def _round() -> None:
    from app.services import bots

    db = session_factory()()
    try:
        out = bots.run_round(db, commit_each=True)
        db.commit()
        if out.get("orders"):
            log.debug("bot round %s", out)
    except Exception:
        db.rollback()
        log.exception("bot round failed")
    finally:
        db.close()


def _loop() -> None:
    interval = get_settings().bot_interval_seconds
    while not _stop.is_set():
        try:
            with get_engine().connect() as lock_conn:
                if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar():
                    lock_conn.rollback()
                    _stop.wait(30)  # another process runs the simulator
                    continue
                lock_conn.commit()
                log.info("market simulator running every %.1fs", interval)
                while not _stop.is_set():
                    started = time.monotonic()
                    _round()
                    took = time.monotonic() - started
                    # Never use more than ~half the CPU: rest at least as long as the round took,
                    # so player requests stay fast on small (free-tier) instances.
                    _stop.wait(max(0.5, interval - took, took))
                lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
                lock_conn.commit()
        except Exception:
            log.exception("simulator connection lost; retrying")
            _stop.wait(10)


def start() -> None:
    global _started
    s = get_settings()
    if _started or not s.bots_enabled or s.env == "test":
        return
    _started = True
    threading.Thread(target=_loop, name="market-simulator", daemon=True).start()
