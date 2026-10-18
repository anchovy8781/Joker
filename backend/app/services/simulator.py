"""Background market simulator: runs bot rounds every few seconds inside the web process.

Exactly one process runs it: the thread holds a PostgreSQL session advisory lock on a
dedicated connection for as long as it lives; other processes keep retrying quietly.
"""

import logging
import random
import threading
import time

from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine, session_factory

log = logging.getLogger("yex.simulator")
LOCK_KEY = 7_123_456_789
_started = False
_stop = threading.Event()


_last_persona_round = 0.0

# Live numbers for the status page: is the simulator running here, how long ticks take and
# how many bot orders went through in the last minute.
stats: dict = {"running": False, "ticks": [], "last_error": None, "last_error_at": None}


def snapshot() -> dict:
    now = time.time()
    recent = [t for t in stats["ticks"] if now - t[0] <= 60]
    took = [t[1] for t in recent]
    return {"running": stats["running"], "ticks_last_minute": len(recent),
            "orders_last_minute": sum(t[2] for t in recent),
            "avg_tick_seconds": round(sum(took) / len(took), 2) if took else None,
            "last_error": stats["last_error"], "last_error_at": stats["last_error_at"]}


def _round() -> None:
    """Every few seconds: the price heartbeat. Every bot_persona_interval_seconds: a full
    round where the big personas (whales, value investors, momentum...) make decisions."""
    global _last_persona_round
    from app import fastpath
    from app.services import bots, runtime_settings

    db = session_factory()()
    started = time.monotonic()
    try:
        runtime_settings.refresh(db)
        if not get_settings().bots_enabled:  # switched off by an administrator
            return
        if time.monotonic() - _last_persona_round >= get_settings().bot_persona_interval_seconds:
            _last_persona_round = time.monotonic()
            out = bots.run_round(db, commit_each=True)
        else:
            out = bots.tape_tick(db, fastpath.watched_assets())
        db.commit()
        stats["ticks"] = [t for t in stats["ticks"] if time.time() - t[0] <= 120] + [
            (time.time(), time.monotonic() - started, int(out.get("orders", 0)))]
        if out.get("orders"):
            log.debug("bot round %s", out)
    except Exception as exc:
        db.rollback()
        stats["last_error"], stats["last_error_at"] = f"{type(exc).__name__}: {str(exc)[:160]}", time.time()
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
                stats["running"] = True
                log.info("market simulator running every %.1fs", get_settings().bot_interval_seconds)
                while not _stop.is_set():
                    started, cpu = time.monotonic(), time.thread_time()
                    _round()
                    took = time.monotonic() - started
                    cpu_used = time.thread_time() - cpu
                    interval = get_settings().bot_interval_seconds
                    # Next tick ~interval ±1s after the previous one started (3-5s by default).
                    # Free instances get a small slice of one CPU (0.1): rest at least 15x the
                    # CPU time used so player requests and health checks are never starved.
                    target = random.uniform(max(1.0, interval - 1), interval + 1)
                    _stop.wait(max(0.3, target - took, cpu_used * 15))
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
