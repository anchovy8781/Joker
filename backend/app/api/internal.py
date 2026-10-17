"""Scheduler tick for serverless/cloud hosting.

In the cloud deployment there is no long-running worker process: Supabase pg_cron calls
POST /api/internal/tick every minute (via pg_net). Each tick enqueues the periodic jobs
(idempotent dedup keys) and runs the due ones within a time budget. Because pg_cron hits
the service every minute, the free web instance also never goes to sleep.
"""

import hmac
import logging
import time

from fastapi import APIRouter, Request

from app.config import get_settings
from app.db import session_factory
from app.errors import AppError
from app.services import jobs

router = APIRouter(prefix="/api/internal", tags=["internal"], include_in_schema=False)
log = logging.getLogger("yex.tick")
TICK_BUDGET_SECONDS = 20.0  # stop starting new jobs after this; leftovers run next tick


@router.post("/tick")
def tick(request: Request):
    secret = get_settings().tick_secret
    sent = request.headers.get("x-tick-secret", "")
    if not secret or not hmac.compare_digest(sent, secret):
        raise AppError(404, "NOT_FOUND", "Not Found")  # indistinguishable from a missing route
    import app.worker  # noqa: F401  (registers job handlers)

    started = time.monotonic()
    ran: list[dict] = []
    db = session_factory()()
    try:
        jobs.schedule_periodic(db)
        jobs.recover_stuck(db)
        while time.monotonic() - started < TICK_BUDGET_SECONDS:
            job = jobs.run_one(db, "tick")
            if job is None:
                break
            ran.append({"id": job.id, "type": job.job_type, "status": job.status})
    finally:
        db.close()
    if ran:
        log.info("tick ran %s", ran)
    return {"ran": ran, "seconds": round(time.monotonic() - started, 2)}
