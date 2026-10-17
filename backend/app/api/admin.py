"""Administrator API. Separate permission check (role='admin') on every route; every
sensitive action records admin id, reason, target and time in the audit log.
There are deliberately no endpoints that edit or delete trades or ledger entries."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.api.serialize import freshness
from app.db import get_db, run_in_transaction
from app.errors import AppError
from app.models import (
    Announcement,
    Appeal,
    AuditLog,
    Channel,
    ChatMessage,
    Job,
    LedgerEntry,
    MarketAsset,
    PriceUpdate,
    ReconciliationRun,
    Report,
    RiskEvent,
    User,
)
from app.schemas import (
    AdjustmentIn,
    AnnouncementIn,
    ChannelRegisterIn,
    ListingApproveIn,
    ReasonIn,
    ResolveIn,
    RiskReviewIn,
    UserStatusIn,
)
from app.security import require_admin, utcnow
from app.services import chat, jobs, listing, reconciliation, risk
from app.services.audit import write_audit
from app.services.ledger import lock_account, post_entry

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("/risk-events")
def risk_events(admin: User = Depends(require_admin), db: Session = Depends(get_db),
                reviewed: bool | None = None, limit: int = Query(default=100, ge=1, le=500)):
    q = select(RiskEvent, User.username).outerjoin(User, User.id == RiskEvent.user_id)
    if reviewed is True:
        q = q.where(RiskEvent.reviewed_at.is_not(None))
    elif reviewed is False:
        q = q.where(RiskEvent.reviewed_at.is_(None))
    return [{"id": e.id, "user_id": e.user_id, "username": name, "asset_id": e.asset_id,
             "event_type": e.event_type, "severity": e.severity, "score": e.score, "reason": e.reason,
             "evidence": e.evidence_json, "created_at": e.created_at, "reviewed_at": e.reviewed_at,
             "resolution": e.resolution}
            for e, name in db.execute(q.order_by(RiskEvent.id.desc()).limit(limit)).all()]


@router.post("/risk-events/{event_id}/review")
def review_risk_event(event_id: int, body: RiskReviewIn, request: Request,
                      admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    ev = db.get(RiskEvent, event_id)
    if ev is None:
        raise AppError(404, "NOT_FOUND", "Risk event not found")
    if ev.reviewed_at is not None:
        raise AppError(409, "ALREADY_REVIEWED", "Risk event already reviewed")
    ev.reviewed_at = utcnow()
    ev.reviewed_by = admin.id
    ev.resolution = f"{body.decision}: {body.resolution}"
    write_audit(db, "risk.review", actor_id=admin.id, target_type="risk_event", target_id=event_id,
                request_id=_rid(request), metadata={"decision": body.decision, "resolution": body.resolution})
    if ev.user_id and body.decision == "dismiss":
        db.flush()
        risk.recompute_user_risk(db, ev.user_id)
    db.commit()
    return {"id": ev.id, "resolution": ev.resolution}


@router.get("/audit-logs")
def audit_logs(admin: User = Depends(require_admin), db: Session = Depends(get_db),
               action: str | None = Query(default=None, max_length=64),
               limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)):
    q = select(AuditLog)
    if action:
        q = q.where(AuditLog.action.startswith(action))
    return [{"id": a.id, "actor_id": a.actor_id, "action": a.action, "target_type": a.target_type,
             "target_id": a.target_id, "request_id": a.request_id, "metadata": a.metadata_json,
             "created_at": a.created_at}
            for a in db.execute(q.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).scalars()]


@router.post("/assets/{asset_id}/halt")
def halt(asset_id: int, body: ReasonIn, request: Request, admin: User = Depends(require_admin),
         db: Session = Depends(get_db)):
    a = run_in_transaction(db, lambda s: listing.halt(s, asset_id, body.reason, admin.id, _rid(request)))
    return {"id": a.id, "trading_status": a.trading_status, "halt_reason": a.halt_reason}


@router.post("/assets/{asset_id}/resume")
def resume(asset_id: int, body: ReasonIn, request: Request, admin: User = Depends(require_admin),
           db: Session = Depends(get_db)):
    a = run_in_transaction(db, lambda s: listing.resume(s, asset_id, body.reason, admin.id, _rid(request)))
    return {"id": a.id, "trading_status": a.trading_status, "halt_reason": a.halt_reason}


@router.get("/channels")
def channels(admin: User = Depends(require_admin), db: Session = Depends(get_db),
             listing_status: Literal["pending", "listed", "rejected", "delisted"] | None = None):
    requester = aliased(User)
    q = (select(Channel, MarketAsset, requester.username)
         .outerjoin(MarketAsset, MarketAsset.channel_id == Channel.id)
         .outerjoin(requester, requester.id == Channel.requested_by))
    if listing_status:
        q = q.where(Channel.listing_status == listing_status)
    return [{"id": c.id, "youtube_channel_id": c.youtube_channel_id, "title": c.title,
             "data_source": c.data_source, "listing_status": c.listing_status,
             "data_quality_status": c.data_quality_status, "freshness": freshness(c).model_dump(),
             "requested_by": req_name, "request_note": c.request_note,
             "asset": None if a is None else {"id": a.id, "symbol": a.symbol,
                                               "trading_status": a.trading_status,
                                               "halt_reason": a.halt_reason,
                                               "reference_price": a.reference_price,
                                               "celebrity_until": a.celebrity_until,
                                               "new_video_until": a.new_video_until}}
            for c, a, req_name in db.execute(q.order_by(Channel.id)).all()]


@router.post("/channels", status_code=201)
def register_channel(body: ChannelRegisterIn, request: Request, admin: User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    ch = run_in_transaction(db, lambda s: listing.register_channel(
        s, youtube_channel_id=body.youtube_channel_id, title=body.title, data_source=body.data_source,
        category=body.category, actor_id=admin.id, request_id=_rid(request)))
    return {"id": ch.id, "listing_status": ch.listing_status}


@router.post("/channels/{channel_id}/approve", status_code=201)
def approve(channel_id: int, body: ListingApproveIn, request: Request,
            admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    def tx(s):
        a = listing.approve_listing(s, channel_id=channel_id, symbol=body.symbol,
                                    initial_price=body.initial_price,
                                    shares_outstanding=body.shares_outstanding, actor_id=admin.id,
                                    request_id=_rid(request),
                                    celebrity_new_channel=body.celebrity_new_channel)
        write_audit(s, "listing.approve_reason", actor_id=admin.id, target_type="asset", target_id=a.id,
                    request_id=_rid(request), metadata={"reason": body.reason})
        return a
    a = run_in_transaction(db, tx)
    return {"asset_id": a.id, "symbol": a.symbol, "trading_status": a.trading_status}


@router.post("/channels/{channel_id}/reject")
def reject(channel_id: int, body: ReasonIn, request: Request, admin: User = Depends(require_admin),
           db: Session = Depends(get_db)):
    ch = run_in_transaction(db, lambda s: listing.reject_listing(s, channel_id, body.reason, admin.id, _rid(request)))
    return {"id": ch.id, "listing_status": ch.listing_status}


@router.get("/jobs")
def list_jobs(admin: User = Depends(require_admin), db: Session = Depends(get_db),
              limit: int = Query(default=50, ge=1, le=200)):
    return [{"id": j.id, "job_type": j.job_type, "status": j.status, "attempts": j.attempts,
             "run_at": j.run_at, "finished_at": j.finished_at, "last_error": j.last_error,
             "result": j.result_json}
            for j in db.execute(select(Job).order_by(Job.id.desc()).limit(limit)).scalars()]


@router.post("/jobs/{job_type}/run", status_code=202)
def run_job(job_type: Literal["collect_data", "price_update", "reconcile", "risk_scan", "mm_requote"],
            request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    payload = {"effective_date": utcnow().date().isoformat()} if job_type == "price_update" else {}
    # price_update keeps its per-day dedup key, so a manual trigger can never double-apply prices.
    key = f"price:{utcnow().date()}" if job_type == "price_update" else f"manual:{job_type}:{uuid.uuid4().hex}"
    jid = jobs.enqueue(db, job_type, payload, dedup_key=key)
    write_audit(db, "job.manual_enqueue", actor_id=admin.id, target_type="job", target_id=jid,
                request_id=_rid(request), metadata={"job_type": job_type, "deduplicated": jid is None})
    db.commit()
    return {"job_id": jid, "deduplicated": jid is None}


@router.get("/price-updates")
def price_updates(admin: User = Depends(require_admin), db: Session = Depends(get_db),
                  status: Literal["applied", "skipped", "failed"] | None = None,
                  limit: int = Query(default=100, ge=1, le=500)):
    q = select(PriceUpdate, MarketAsset.symbol).join(MarketAsset, MarketAsset.id == PriceUpdate.asset_id)
    if status:
        q = q.where(PriceUpdate.status == status)
    return [{"id": p.id, "symbol": sym, "asset_id": p.asset_id, "effective_date": p.effective_date,
             "status": p.status, "old_price": p.old_price, "new_price": p.new_price,
             "daily_return": p.daily_return, "error": p.error, "inputs": p.inputs_json}
            for p, sym in db.execute(q.order_by(PriceUpdate.id.desc()).limit(limit)).all()]


@router.get("/reconciliation")
def reconciliation_runs(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": r.id, "status": r.status, "checked_accounts": r.checked_accounts,
             "mismatches": r.mismatches_json, "totals": r.totals_json, "created_at": r.created_at}
            for r in db.execute(select(ReconciliationRun).order_by(ReconciliationRun.id.desc()).limit(30)).scalars()]


@router.post("/reconciliation/run")
def run_reconciliation(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    run = reconciliation.reconcile(db)
    write_audit(db, "economy.reconcile_manual", actor_id=admin.id, target_type="reconciliation_run",
                target_id=run.id, request_id=_rid(request))
    db.commit()
    return {"id": run.id, "status": run.status, "mismatches": run.mismatches_json, "totals": run.totals_json}


@router.post("/users/{user_id}/status")
def set_user_status(user_id: int, body: UserStatusIn, request: Request,
                    admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None or u.role != "user":
        raise AppError(404, "NOT_FOUND", "User not found")
    before = u.status
    u.status = body.status
    write_audit(db, "user.status_change", actor_id=admin.id, target_type="user", target_id=user_id,
                request_id=_rid(request), metadata={"from": before, "to": body.status, "reason": body.reason})
    db.commit()
    return {"id": u.id, "status": u.status}


@router.post("/adjustments", status_code=201)
def adjustment(body: AdjustmentIn, request: Request, admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    """Exceptional operational correction: a new ledger entry with reason + admin id.
    It never edits existing entries and cannot make a balance negative."""
    if body.amount == 0:
        raise AppError(422, "INVALID_AMOUNT", "Adjustment amount must be non-zero")
    ref = f"adj:{body.idempotency_key}"

    def tx(s: Session):
        if s.execute(select(LedgerEntry.id).where(LedgerEntry.reference_type == "admin_adjustment",
                                                  LedgerEntry.reference_id == ref)).first():
            raise AppError(409, "DUPLICATE_ADJUSTMENT", "Adjustment already applied")
        target = s.get(User, body.user_id)
        if target is None or target.role != "user":
            raise AppError(404, "NOT_FOUND", "User not found")
        acct = lock_account(s, body.user_id)
        e = post_entry(s, acct, entry_type="admin_adjustment", amount=body.amount, transaction_id=uuid.uuid4(),
                       reference_type="admin_adjustment", reference_id=ref, actor_id=admin.id,
                       memo=body.reason)
        write_audit(s, "economy.admin_adjustment", actor_id=admin.id, target_type="user",
                    target_id=body.user_id, request_id=_rid(request),
                    metadata={"amount": body.amount, "reason": body.reason, "balance_after": e.balance_after})
        return e

    e = run_in_transaction(db, tx)
    return {"ledger_entry_id": e.id, "balance_after": e.balance_after}


@router.post("/announcements", status_code=201)
def announce(body: AnnouncementIn, request: Request, admin: User = Depends(require_admin),
             db: Session = Depends(get_db)):
    a = Announcement(title=body.title, body=body.body, created_by=admin.id)
    db.add(a)
    db.flush()
    write_audit(db, "announcement.create", actor_id=admin.id, target_type="announcement", target_id=a.id,
                request_id=_rid(request))
    db.commit()
    return {"id": a.id}


@router.get("/reports")
def reports(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": r.id, "reporter_id": r.reporter_id, "target_type": r.target_type, "target_id": r.target_id,
             "reason": r.reason, "status": r.status, "created_at": r.created_at, "resolution": r.resolution}
            for r in db.execute(select(Report).order_by(Report.id.desc()).limit(200)).scalars()]


@router.post("/reports/{report_id}/resolve")
def resolve_report(report_id: int, body: ResolveIn, request: Request, admin: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    r = db.get(Report, report_id)
    if r is None:
        raise AppError(404, "NOT_FOUND", "Report not found")
    if body.status not in ("resolved", "dismissed"):
        raise AppError(422, "INVALID_STATUS", "Reports resolve to resolved or dismissed")
    r.status, r.resolution, r.handled_by, r.handled_at = body.status, body.resolution, admin.id, utcnow()
    write_audit(db, "report.resolve", actor_id=admin.id, target_type="report", target_id=report_id,
                request_id=_rid(request), metadata={"status": body.status, "resolution": body.resolution})
    db.commit()
    return {"id": r.id, "status": r.status}


@router.get("/appeals")
def appeals(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": a.id, "user_id": a.user_id, "risk_event_id": a.risk_event_id, "message": a.message,
             "status": a.status, "created_at": a.created_at, "resolution": a.resolution}
            for a in db.execute(select(Appeal).order_by(Appeal.id.desc()).limit(200)).scalars()]


@router.post("/appeals/{appeal_id}/resolve")
def resolve_appeal(appeal_id: int, body: ResolveIn, request: Request, admin: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    a = db.get(Appeal, appeal_id)
    if a is None:
        raise AppError(404, "NOT_FOUND", "Appeal not found")
    if body.status not in ("accepted", "rejected"):
        raise AppError(422, "INVALID_STATUS", "Appeals resolve to accepted or rejected")
    a.status, a.resolution, a.reviewed_by, a.reviewed_at = body.status, body.resolution, admin.id, utcnow()
    if body.status == "accepted":
        # Accepting an appeal clears the linked event (or all open events) and lifts restrictions.
        q = select(RiskEvent).where(RiskEvent.user_id == a.user_id, RiskEvent.reviewed_at.is_(None))
        if a.risk_event_id:
            q = q.where(RiskEvent.id == a.risk_event_id)
        for ev in db.execute(q).scalars():
            ev.reviewed_at, ev.reviewed_by, ev.resolution = utcnow(), admin.id, f"appeal accepted: {body.resolution}"
        u = db.get(User, a.user_id)
        if u.status == "restricted":
            u.status = "active"
        db.flush()
        risk.recompute_user_risk(db, a.user_id)
    write_audit(db, "appeal.resolve", actor_id=admin.id, target_type="appeal", target_id=appeal_id,
                request_id=_rid(request), metadata={"status": body.status, "resolution": body.resolution})
    db.commit()
    return {"id": a.id, "status": a.status}


@router.get("/chat")
def chat_moderation(admin: User = Depends(require_admin), db: Session = Depends(get_db),
                    room: str | None = Query(default=None, max_length=32),
                    limit: int = Query(default=100, ge=1, le=500)):
    q = select(ChatMessage, User.username).join(User, User.id == ChatMessage.user_id)
    if room:
        q = q.where(ChatMessage.room == room)
    return [{"id": m.id, "room": m.room, "user_id": m.user_id, "username": name, "body": m.body,
             "created_at": m.created_at, "hidden_at": m.hidden_at, "hidden_reason": m.hidden_reason}
            for m, name in db.execute(q.order_by(ChatMessage.id.desc()).limit(limit)).all()]


@router.post("/chat/{message_id}/hide")
def hide_chat(message_id: int, body: ReasonIn, request: Request, admin: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    msg = chat.hide_message(db, message_id, admin.id, body.reason, _rid(request))
    db.commit()
    return {"id": msg.id, "hidden": msg.hidden_at is not None}
