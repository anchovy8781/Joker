"""Background worker: `python -m app.worker`. Runs scheduled jobs from the PostgreSQL queue.

Several workers may run at once; SKIP LOCKED + dedup keys prevent double execution.
"""

import logging
import os
import signal
import socket
import time
from datetime import date

from sqlalchemy import select

from app.db import session_factory
from app.models import Channel, MarketAsset
from app.services import collector, jobs, pricing, reconciliation, risk
from app.services.listing import requote_market_maker
from app.services.youtube import get_api_provider

log = logging.getLogger("yex.worker")


@jobs.handler("collect_data")
def _collect(db, payload):
    return collector.collect_all(db, api=get_api_provider())


@jobs.handler("price_update")
def _price(db, payload):
    eff = date.fromisoformat(payload["effective_date"]) if payload.get("effective_date") else None
    summary = pricing.run_daily_update(db, eff or pricing.utcnow().date())
    db.flush()
    # Re-centre market-maker quotes on the new reference prices.
    _requote(db, {})
    return summary


@jobs.handler("reconcile")
def _reconcile(db, payload):
    run = reconciliation.reconcile(db)
    return {"run_id": run.id, "status": run.status}


@jobs.handler("risk_scan")
def _risk(db, payload):
    return risk.scan(db)


@jobs.handler("mm_requote")
def _requote(db, payload):
    ids = db.execute(
        select(MarketAsset.id).join(Channel, Channel.id == MarketAsset.channel_id)
        .where(Channel.listing_status == "listed", MarketAsset.trading_status == "active")
        .order_by(MarketAsset.id)
    ).scalars().all()
    placed = 0
    for aid in ids:
        placed += requote_market_maker(db, aid)["placed"]
    return {"assets": len(ids), "orders_placed": placed}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker %s started", worker_id)
    last_schedule = 0.0
    while not stop:
        db = session_factory()()
        try:
            if time.monotonic() - last_schedule > 30:
                jobs.schedule_periodic(db)
                jobs.recover_stuck(db)
                last_schedule = time.monotonic()
            job = jobs.run_one(db, worker_id)
            if job is not None:
                log.info("job %s %s -> %s", job.id, job.job_type, job.status)
                continue
        except Exception:
            log.exception("worker loop error")
            db.rollback()
        finally:
            db.close()
        time.sleep(2)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
