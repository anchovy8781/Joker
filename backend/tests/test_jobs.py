"""PostgreSQL job queue + worker handlers."""

from sqlalchemy import func, select

import app.worker  # noqa: F401  (registers handlers)
from app.models import Job, MarketAsset, PriceUpdate, ReconciliationRun
from app.security import utcnow
from app.services import jobs


def test_schedule_is_idempotent(db):
    first = jobs.schedule_periodic(db)
    second = jobs.schedule_periodic(db)
    assert len(first) == 8 and second == []
    assert db.execute(select(func.count(Job.id))).scalar_one() == 8


def test_worker_runs_jobs_end_to_end(db, asset):
    day = utcnow().date().isoformat()
    for t, payload in (("collect_data", {}), ("price_update", {"effective_date": day}),
                       ("reconcile", {}), ("risk_scan", {}), ("mm_requote", {})):
        jid = jobs.enqueue(db, t, payload, dedup_key=f"t:{t}")
        db.commit()
        job = jobs.run_one(db, "test", jid)
        assert job.status == "succeeded", (t, job.last_error)
    assert db.execute(select(PriceUpdate.status)).scalar_one() == "applied"
    assert db.execute(select(ReconciliationRun.status)).scalar_one() == "ok"
    db.expire_all()
    assert db.get(MarketAsset, asset).price_updated_at is not None


def test_failed_job_is_retried_then_marked_failed(db):
    @jobs.handler("always_fails")
    def _boom(db, payload):
        raise ValueError("nope")

    jid = jobs.enqueue(db, "always_fails", {}, dedup_key="fail-1", max_attempts=2)
    db.commit()
    job = jobs.run_one(db, "t", jid)
    assert job.status == "queued" and "nope" in job.last_error
    job.run_at = utcnow()
    db.commit()
    job = jobs.run_one(db, "t", jid)
    assert job.status == "failed" and job.attempts == 2
