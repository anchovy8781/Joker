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

from app.db import run_in_transaction, session_factory
from app.models import Channel, MarketAsset, Order
from app.services import alerts, auto_orders, collector, jobs, live, market_hours, orders, pricing, reconciliation, risk
from app.services.listing import requote_market_maker
from app.services.youtube import get_api_provider

log = logging.getLogger("yex.worker")


@jobs.handler("collect_data")
def _collect(db, payload):
    return collector.collect_all(db, api=get_api_provider())


@jobs.handler("collect_live")
def _collect_live(db, payload):
    return live.collect_live(db, get_api_provider())


@jobs.handler("price_alerts")
def _alerts(db, payload):
    return {"fired": alerts.check_all(db)}


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
    from app.services.listing import resume_auto_halts

    resume_auto_halts(db)
    ids = db.execute(
        select(MarketAsset.id).join(Channel, Channel.id == MarketAsset.channel_id)
        .where(Channel.listing_status == "listed", MarketAsset.trading_status == "active")
        .order_by(MarketAsset.id)
    ).scalars().all()
    placed = 0
    for aid in ids:
        placed += requote_market_maker(db, aid)["placed"]
    return {"assets": len(ids), "orders_placed": placed}


@jobs.handler("activate_reservations")
def _activate(db, payload):
    """At session open, submit reservations to the book oldest-first, one transaction each."""
    if not market_hours.is_open(pricing.utcnow()):
        return {"market_open": False, "activated": 0}
    ids = db.execute(select(Order.id).where(Order.status == "scheduled").order_by(Order.id)).scalars().all()
    if ids:
        _requote(db, {})  # put market-maker liquidity on the book before reservations arrive
    db.commit()
    activated = 0
    for oid in ids:
        run_in_transaction(db, lambda s, oid=oid: orders.activate_scheduled(s, oid))
        activated += 1
    if ids:
        auto_orders.evaluate(db)
        db.commit()
    return {"market_open": True, "activated": activated}


@jobs.handler("auto_orders")
def _auto(db, payload):
    return auto_orders.evaluate(db)


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
