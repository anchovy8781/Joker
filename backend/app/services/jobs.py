"""PostgreSQL job queue: enqueue with a dedup key, claim with FOR UPDATE SKIP LOCKED."""

import json
import logging
import traceback
from collections.abc import Callable
from datetime import date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Job
from app.security import utcnow

log = logging.getLogger("yex.jobs")

HANDLERS: dict[str, Callable[[Session, dict], dict]] = {}


def handler(name: str):
    def deco(fn):
        HANDLERS[name] = fn
        return fn
    return deco


def enqueue(db: Session, job_type: str, payload: dict | None = None, *, dedup_key: str | None = None,
            run_at: datetime | None = None, max_attempts: int = 3) -> int | None:
    """Insert a job unless one with the same dedup_key already exists. Returns id or None."""
    row = db.execute(
        text(
            "INSERT INTO jobs (job_type, payload, status, dedup_key, run_at, attempts, max_attempts) "
            "VALUES (:t, CAST(:p AS jsonb), 'queued', :k, :r, 0, :m) "
            "ON CONFLICT (dedup_key) DO NOTHING RETURNING id"
        ),
        {"t": job_type, "p": json.dumps(payload or {}), "k": dedup_key,
         "r": run_at or utcnow(), "m": max_attempts},
    ).scalar()
    return row


def claim(db: Session, worker_id: str, job_id: int | None = None) -> Job | None:
    q = select(Job).where(Job.status == "queued")
    q = q.where(Job.id == job_id) if job_id is not None else q.where(Job.run_at <= utcnow())
    job = db.execute(
        q.order_by(Job.run_at, Job.id).limit(1).with_for_update(skip_locked=True).execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if job is None:
        return None
    job.status = "running"
    job.locked_at = utcnow()
    job.locked_by = worker_id
    job.attempts += 1
    db.commit()
    return job


def run_one(db: Session, worker_id: str, job_id: int | None = None) -> Job | None:
    job = claim(db, worker_id, job_id)
    if job is None:
        return None
    fn = HANDLERS.get(job.job_type)
    try:
        if fn is None:
            raise RuntimeError(f"no handler for {job.job_type}")
        result = fn(db, job.payload)
        db.commit()
        job = db.get(Job, job.id)
        job.status = "succeeded"
        job.result_json = result
        job.finished_at = utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("job %s (%s) failed: %s", job.id, job.job_type, exc)
        job = db.get(Job, job.id)
        job.last_error = "".join(traceback.format_exception_only(exc))[:4000]
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.finished_at = utcnow()
        else:
            job.status = "queued"
            job.run_at = utcnow() + timedelta(seconds=30 * 2 ** job.attempts)
        db.commit()
    return job


def recover_stuck(db: Session, older_than: timedelta = timedelta(minutes=15)) -> int:
    """Re-queue jobs whose worker died mid-run."""
    n = db.execute(
        text("UPDATE jobs SET status = 'queued', locked_by = NULL WHERE status = 'running' "
             "AND locked_at < :t AND attempts < max_attempts"),
        {"t": utcnow() - older_than},
    ).rowcount
    db.commit()
    return n


def schedule_periodic(db: Session, now: datetime | None = None) -> list[int]:
    """Idempotently enqueue the recurring jobs (dedup keys make this safe to call often)."""
    now = now or utcnow()
    today: date = now.date()
    created = []
    slot6 = now.hour // 6
    plan = [
        ("collect_data", {}, f"collect:{today}:{slot6}", now),
        # Daily price update after the first collection of the day (01:00 UTC).
        ("price_update", {"effective_date": today.isoformat()}, f"price:{today}",
         datetime.combine(today, datetime.min.time(), now.tzinfo) + timedelta(hours=1)),
        ("reconcile", {}, f"reconcile:{today}",
         datetime.combine(today, datetime.min.time(), now.tzinfo) + timedelta(hours=2)),
        ("risk_scan", {}, f"risk:{now.strftime('%Y-%m-%dT%H')}:{now.minute // 15}", now),
        ("mm_requote", {}, f"mm:{now.strftime('%Y-%m-%dT%H')}:{now.minute // 10}", now),
        ("collect_live", {}, f"live:{now.strftime('%Y-%m-%dT%H')}:"
         f"{now.minute // max(1, get_settings().live_stats_interval_minutes)}", now),
        ("activate_reservations", {}, f"activate:{now.strftime('%Y-%m-%dT%H:%M')}", now),
        ("auto_orders", {}, f"auto:{now.strftime('%Y-%m-%dT%H:%M')}", now),
    ]
    for job_type, payload, key, run_at in plan:
        jid = enqueue(db, job_type, payload, dedup_key=key, run_at=run_at)
        if jid:
            created.append(jid)
    db.commit()
    return created
