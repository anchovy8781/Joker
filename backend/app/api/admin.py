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
    QuickListIn,
    SettingIn,
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
from app.services import channel_lookup, chat, runtime_settings, jobs, listing, reconciliation, risk
from app.services.audit import write_audit
from app.services.ledger import lock_account, post_entry
from app.services.youtube import get_api_provider

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
    from app import fastpath

    fastpath.clear_responses()
    return {"id": a.id, "trading_status": a.trading_status, "halt_reason": a.halt_reason}


@router.post("/assets/{asset_id}/resume")
def resume(asset_id: int, body: ReasonIn, request: Request, admin: User = Depends(require_admin),
           db: Session = Depends(get_db)):
    a = run_in_transaction(db, lambda s: listing.resume(s, asset_id, body.reason, admin.id, _rid(request)))
    from app import fastpath

    fastpath.clear_responses()
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
             "handle": c.handle, "thumbnail_url": c.thumbnail_url,
             "data_source": c.data_source, "listing_status": c.listing_status,
             "data_quality_status": c.data_quality_status, "freshness": freshness(c).model_dump(),
             "requested_by": req_name, "request_note": c.request_note,
             "asset": None if a is None else {"id": a.id, "symbol": a.symbol,
                                               "trading_status": a.trading_status,
                                               "halt_reason": a.halt_reason,
                                               "reference_price": a.reference_price,
                                               "current_price": a.current_price,
                                               "celebrity_until": a.celebrity_until,
                                               "new_video_until": a.new_video_until}}
            for c, a, req_name in db.execute(q.order_by(Channel.id)).all()]


@router.post("/channels", status_code=201)
def register_channel(body: ChannelRegisterIn, request: Request, admin: User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    cid, title = body.youtube_channel_id.strip(), body.title
    if body.data_source == "youtube_api":
        cid, found_title = channel_lookup.resolve(cid, get_api_provider())
        title = title or found_title or cid
    elif not title:
        raise AppError(422, "TITLE_REQUIRED", "데모 채널은 이름이 필요합니다")
    ch = run_in_transaction(db, lambda s: listing.register_channel(
        s, youtube_channel_id=cid, title=title, data_source=body.data_source,
        category=body.category, actor_id=admin.id, request_id=_rid(request)))
    return {"id": ch.id, "listing_status": ch.listing_status, "youtube_channel_id": cid, "title": title}


def _approve_with_suggestion(s: Session, ch: Channel, admin: User, request: Request, reason: str,
                             celebrity: bool = False, symbol=None, price=None, shares=None):
    sug = channel_lookup.suggest_listing(s, ch)
    a = listing.approve_listing(s, channel_id=ch.id, symbol=symbol or sug["symbol"],
                                initial_price=price or sug["initial_price"],
                                shares_outstanding=shares or sug["shares_outstanding"],
                                actor_id=admin.id, request_id=_rid(request), celebrity_new_channel=celebrity)
    write_audit(s, "listing.approve_reason", actor_id=admin.id, target_type="asset", target_id=a.id,
                request_id=_rid(request), metadata={"reason": reason})
    return a


@router.get("/settings")
def get_app_settings(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return runtime_settings.listing(db)


@router.post("/settings/{key}")
def set_app_setting(key: str, body: SettingIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """One-touch switch for a runtime setting (audited)."""
    uid = admin.id
    value = run_in_transaction(db, lambda s: runtime_settings.set_value(s, key, body.value, uid))
    return {"key": key, "value": value}


@router.post("/quick-list", status_code=201)
def quick_list(body: QuickListIn, request: Request, admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    """원터치 상장: handle/URL → look up → register (if new) → list with suggested terms."""
    info = channel_lookup.preview(db, body.channel, get_api_provider())
    if info["listing_status"] == "listed":
        raise AppError(409, "ALREADY_LISTED", "이미 상장된 채널입니다", {"asset_id": info.get("asset_id")})
    admin_id = admin.id

    def tx(s: Session):
        ch = s.execute(select(Channel).where(Channel.youtube_channel_id == info["youtube_channel_id"])).scalar_one_or_none()
        if ch is None:
            ch = listing.register_channel(s, youtube_channel_id=info["youtube_channel_id"], title=info["title"][:200],
                                          data_source="youtube_api", category=None, actor_id=admin_id,
                                          request_id=_rid(request))
        elif ch.listing_status == "rejected":
            ch.listing_status = "pending"
        ch.handle = info["handle"] or ch.handle
        ch.thumbnail_url = info["thumbnail_url"] or ch.thumbnail_url
        if info["subscriber_count"] is not None:
            listing.store_snapshot(s, ch, _snapshot_from(info), utcnow())
        return _approve_with_suggestion(s, ch, s.get(User, admin_id), request, "원터치 상장",
                                        body.celebrity_new_channel)
    a = run_in_transaction(db, tx)
    return {"asset_id": a.id, "symbol": a.symbol, "initial_price": a.reference_price, "title": info["title"],
            "thumbnail_url": info["thumbnail_url"]}


def _snapshot_from(info: dict):
    from app.services.youtube import ChannelSnapshot

    return ChannelSnapshot(youtube_channel_id=info["youtube_channel_id"], title=info["title"],
                           thumbnail_url=info["thumbnail_url"], handle=info["handle"],
                           subscriber_count=info["subscriber_count"], view_count=info["view_count"] or 0,
                           video_count=info["video_count"] or 0, source_timestamp=None,
                           hidden_subscriber_count=info["hidden_subscriber_count"])


@router.post("/channels/approve-all")
def approve_all(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Approve every pending listing with suggested terms (one tap)."""
    ids = db.execute(select(Channel.id).where(Channel.listing_status == "pending").order_by(Channel.id)).scalars().all()
    listed = []
    for cid in ids:
        a = run_in_transaction(db, lambda s, cid=cid: _approve_with_suggestion(
            s, s.get(Channel, cid), s.get(User, admin.id), request, "원터치 일괄 승인"))
        listed.append({"asset_id": a.id, "symbol": a.symbol})
    return {"approved": len(listed), "assets": listed}


@router.get("/channels/{channel_id}/suggestion")
def listing_suggestion(channel_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    ch = db.get(Channel, channel_id)
    if ch is None:
        raise AppError(404, "CHANNEL_NOT_FOUND", "Channel not found")
    return channel_lookup.suggest_listing(db, ch)


@router.post("/channels/{channel_id}/approve", status_code=201)
def approve(channel_id: int, body: ListingApproveIn, request: Request,
            admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    def tx(s):
        ch = s.get(Channel, channel_id)
        if ch is None:
            raise AppError(404, "CHANNEL_NOT_FOUND", "Channel not found")
        return _approve_with_suggestion(s, ch, admin, request, body.reason, body.celebrity_new_channel,
                                        body.symbol, body.initial_price, body.shares_outstanding)
    a = run_in_transaction(db, tx)
    return {"asset_id": a.id, "symbol": a.symbol, "trading_status": a.trading_status,
            "initial_price": a.reference_price, "shares_outstanding": a.shares_outstanding}


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


# ---------------------------------------------------------------- operations (one-touch)


@router.post("/assets/{asset_id}/delist")
def delist(asset_id: int, body: ReasonIn, request: Request, admin: User = Depends(require_admin),
           db: Session = Depends(get_db)):
    """상장폐지: stop trading for good and pay every holder out at the last price."""
    out = run_in_transaction(db, lambda s: listing.delist(s, asset_id, body.reason, admin.id, _rid(request)))
    from app import fastpath

    fastpath.clear_responses()  # market lists must drop the stock right away
    return out


HALT_ALL_TAG = "전체정지: "


@router.post("/market/halt-all")
def halt_all(body: ReasonIn, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Emergency stop: halt every active listed stock (resume-all undoes it)."""
    ids = db.execute(select(MarketAsset.id).join(Channel, Channel.id == MarketAsset.channel_id).where(
        Channel.listing_status == "listed", MarketAsset.trading_status == "active")).scalars().all()
    for aid in ids:
        run_in_transaction(db, lambda s, aid=aid: listing.halt(s, aid, f"{HALT_ALL_TAG}{body.reason}", admin.id, _rid(request)))
    from app import fastpath

    fastpath.clear_responses()
    return {"halted": len(ids)}


@router.post("/market/resume-all")
def resume_all(body: ReasonIn, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    # Only what 전체 거래 정지 stopped: individual investigation halts stay as they are.
    ids = db.execute(select(MarketAsset.id).join(Channel, Channel.id == MarketAsset.channel_id).where(
        Channel.listing_status == "listed", MarketAsset.trading_status == "halted",
        MarketAsset.halt_reason.startswith(HALT_ALL_TAG))).scalars().all()
    resumed, failed = [], []
    for aid in ids:
        try:
            run_in_transaction(db, lambda s, aid=aid: listing.resume(s, aid, body.reason, admin.id, _rid(request)))
            resumed.append(aid)
        except AppError as exc:
            failed.append({"asset_id": aid, "reason": exc.message})
    from app import fastpath

    fastpath.clear_responses()
    return {"resumed": len(resumed), "failed": failed}


@router.get("/overview")
def overview(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """운영 현황 at a glance."""
    from datetime import timedelta

    from sqlalchemy import func, text

    from app.models import Account, MarketBar

    now = utcnow()
    day = now - timedelta(hours=24)
    people = db.execute(select(func.count(User.id), func.count(User.id).filter(User.created_at >= day),
                               func.count(User.id).filter(User.last_login_at >= day),
                               func.count(User.id).filter(User.status != "active"))
                        .where(User.role == "user")).one()
    cash = db.execute(select(func.coalesce(func.sum(Account.cash_balance), 0)).join(User, User.id == Account.user_id)
                      .where(User.role == "user")).scalar_one()
    vol = db.execute(select(func.coalesce(func.sum(MarketBar.trade_count), 0), func.coalesce(func.sum(MarketBar.turnover), 0))
                     .where(MarketBar.interval == "1m", MarketBar.timestamp >= day)).one()
    people_trades = db.execute(text(
        "SELECT count(*) FROM trades t JOIN users b ON b.id = t.buyer_id JOIN users s ON s.id = t.seller_id "
        "WHERE t.executed_at >= :d AND (b.role = 'user' OR s.role = 'user')"), {"d": day}).scalar_one()
    assets = db.execute(select(func.count(MarketAsset.id), func.count(MarketAsset.id).filter(MarketAsset.trading_status == "halted"))
                        .join(Channel, Channel.id == MarketAsset.channel_id).where(Channel.listing_status == "listed")).one()
    pending = db.execute(select(func.count(Channel.id)).where(Channel.listing_status == "pending")).scalar_one()
    reports = db.execute(select(func.count(Report.id)).where(Report.status == "open")).scalar_one()
    risks = db.execute(select(func.count(RiskEvent.id)).where(RiskEvent.reviewed_at.is_(None))).scalar_one()
    return {"users": {"total": people[0], "new_24h": people[1], "active_24h": people[2], "restricted": people[3]},
            "user_cash": int(cash),
            "trades_24h": int(vol[0]), "turnover_24h": int(vol[1]), "player_trades_24h": int(people_trades),
            "listed": assets[0], "halted": assets[1], "pending_requests": pending,
            "open_reports": reports, "open_risk_events": risks}


@router.get("/users")
def users(q: str | None = Query(default=None, max_length=32), limit: int = Query(default=50, ge=1, le=200),
          admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Players with balance, holdings value and status (search by name)."""
    from sqlalchemy import func

    from app.models import Account, Position

    holdings = (select(Position.user_id, func.coalesce(func.sum(Position.quantity * MarketAsset.current_price), 0).label("v"))
                .join(MarketAsset, MarketAsset.id == Position.asset_id).group_by(Position.user_id).subquery())
    stmt = (select(User, Account.cash_balance, func.coalesce(holdings.c.v, 0))
            .join(Account, Account.user_id == User.id).outerjoin(holdings, holdings.c.user_id == User.id)
            .where(User.role.in_(("user", "admin"))))
    if q:
        stmt = stmt.where(User.normalized_username.contains(q.lower()))
    rows = db.execute(stmt.order_by((Account.cash_balance + func.coalesce(holdings.c.v, 0)).desc()).limit(limit)).all()
    return [{"id": u.id, "username": u.username, "role": u.role, "status": u.status, "cash": int(c),
             "holdings_value": int(h), "equity": int(c) + int(h), "created_at": u.created_at,
             "last_login_at": u.last_login_at} for u, c, h in rows]



@router.post("/assets/{asset_id}/symbol")
def rename_symbol(asset_id: int, request: Request, body: dict | None = None,
                  admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Change a stock's ticker. Without a symbol, a readable one is made from the channel's
    handle or (romanized) name — e.g. YTVZHOMW → BOTTA."""
    import re as _re

    from app.services.listing import SYMBOL_RE

    def tx(s: Session):
        a = s.get(MarketAsset, asset_id)
        if a is None:
            raise AppError(404, "ASSET_NOT_FOUND", "Asset not found")
        wanted = _re.sub(r"[^A-Z0-9]", "", str((body or {}).get("symbol") or "").upper())
        symbol = wanted or channel_lookup.unique_symbol(s, channel_lookup.symbol_base(s.get(Channel, a.channel_id)), a.id)
        if not SYMBOL_RE.match(symbol):
            raise AppError(422, "INVALID_SYMBOL", "심볼은 영문 대문자·숫자 2~12자예요")
        taken = s.execute(select(MarketAsset.id).where(MarketAsset.symbol == symbol)).scalar()
        if taken is not None and taken != a.id:
            raise AppError(409, "SYMBOL_TAKEN", "이미 쓰이는 심볼이에요")
        before, a.symbol = a.symbol, symbol
        write_audit(s, "asset.rename_symbol", actor_id=admin.id, target_type="asset", target_id=a.id,
                    request_id=_rid(request), metadata={"from": before, "to": symbol})
        return a

    a = run_in_transaction(db, tx)
    from app import fastpath

    fastpath.clear_responses()
    return {"id": a.id, "symbol": a.symbol}
