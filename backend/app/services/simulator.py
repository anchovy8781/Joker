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
    from app.services import bots, runtime_settings

    db = session_factory()()
    try:
        runtime_settings.refresh(db)
        if not get_settings().bots_enabled:  # switched off by an administrator
            return
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
    while not _stop.is_set():
        try:
            with get_engine().connect() as lock_conn:
                if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar():
                    lock_conn.rollback()
                    _stop.wait(30)  # another process runs the simulator
                    continue
                lock_conn.commit()
                log.info("market simulator running every %.1fs", get_settings().bot_interval_seconds)
                while not _stop.is_set():
                    started, cpu = time.monotonic(), time.thread_time()
                    _round()
                    took = time.monotonic() - started
                    cpu_used = time.thread_time() - cpu
                    interval = get_settings().bot_interval_seconds
                    # Free instances get a small slice of one CPU (0.1). Keep the simulator to a
                    # few percent of a core and never busier than half the wall time, so player
                    # requests and health checks are never starved.
                    _stop.wait(max(0.5, interval - took, took, cpu_used * 30))
                lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
                lock_conn.commit()
        except Exception:
            log.exception("simulator connection lost; retrying")
            _stop.wait(10)


def start() -> None:
    global _started
    s = get_settings()
    if _started or s.env == "test":  # the loop itself honours bots_enabled (admin switch)
        return
    _started = True
    threading.Thread(target=_loop, name="market-simulator", daemon=True).start()
