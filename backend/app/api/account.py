"""Authenticated user endpoints. Every query is scoped to the session user (no IDOR)."""

from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account, Appeal, Channel, LedgerEntry, MarketAsset, Position, Report, RiskEvent, Trade, User
from app.schemas import AccountOut, AppealIn, LedgerOut, MyTradeOut, PositionOut, ReportIn
from app.security import require_user
from app.services.audit import write_audit

router = APIRouter(prefix="/api", tags=["account"])


def _positions(db: Session, user_id: int) -> list[PositionOut]:
    rows = db.execute(
        select(Position, MarketAsset, Channel)
        .join(MarketAsset, MarketAsset.id == Position.asset_id)
        .join(Channel, Channel.id == MarketAsset.channel_id)
        .where(Position.user_id == user_id)
        .where(or_(Position.quantity > 0, Position.realized_pnl != 0))
        .order_by(MarketAsset.symbol)
    ).all()
    out = []
    for p, a, c in rows:
        mv = p.quantity * a.current_price
        out.append(PositionOut(
            asset_id=a.id, symbol=a.symbol, title=c.title, is_demo=c.data_source == "demo",
            quantity=p.quantity, reserved_quantity=p.reserved_quantity,
            average_cost=p.average_cost.quantize(Decimal("0.01")), current_price=a.current_price,
            market_value=mv,
            unrealized_pnl=(Decimal(mv) - p.average_cost * p.quantity).quantize(Decimal("0.01")),
            realized_pnl=p.realized_pnl.quantize(Decimal("0.01")),
        ))
    return out


@router.get("/account", response_model=AccountOut)
def account(user: User = Depends(require_user), db: Session = Depends(get_db)):
    acct = db.get(Account, user.id)
    positions = _positions(db, user.id)
    pv = sum(p.market_value for p in positions)
    return AccountOut(
        user_id=user.id,
        cash_balance=acct.cash_balance,
        reserved_cash=acct.reserved_cash,
        available_cash=acct.cash_balance - acct.reserved_cash,
        positions_value=pv,
        total_equity=acct.cash_balance + pv,
        realized_pnl=sum((p.realized_pnl for p in positions), Decimal(0)),
        unrealized_pnl=sum((p.unrealized_pnl for p in positions if p.quantity), Decimal(0)),
        positions_count=sum(1 for p in positions if p.quantity > 0),
    )


@router.get("/account/positions", response_model=list[PositionOut])
def positions(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return _positions(db, user.id)


@router.get("/account/ledger", response_model=list[LedgerOut])
def ledger(user: User = Depends(require_user), db: Session = Depends(get_db),
           limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)):
    return db.execute(
        select(LedgerEntry).where(LedgerEntry.user_id == user.id)
        .order_by(LedgerEntry.id.desc()).limit(limit).offset(offset)
    ).scalars().all()


@router.get("/trades", response_model=list[MyTradeOut])
def my_trades(user: User = Depends(require_user), db: Session = Depends(get_db),
              asset_id: int | None = None, limit: int = Query(default=100, ge=1, le=500)):
    q = (select(Trade, MarketAsset.symbol).join(MarketAsset, MarketAsset.id == Trade.asset_id)
         .where(or_(Trade.buyer_id == user.id, Trade.seller_id == user.id)))
    if asset_id:
        q = q.where(Trade.asset_id == asset_id)
    out = []
    for t, symbol in db.execute(q.order_by(Trade.id.desc()).limit(limit)).all():
        is_buyer = t.buyer_id == user.id
        out.append(MyTradeOut(
            id=t.id, trade_uid=t.trade_uid, asset_id=t.asset_id, quantity=t.quantity,
            execution_price=t.execution_price, taker_side=t.taker_side, executed_at=t.executed_at,
            side="buy" if is_buyer else "sell", fee=t.buyer_fee if is_buyer else t.seller_fee,
            fee_waived=(not is_buyer) and t.seller_fee_waived,
            realized_pnl=None if is_buyer else t.seller_realized_pnl.quantize(Decimal("0.01")),
            symbol=symbol,
        ))
    return out


@router.get("/account/risk")
def my_risk(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Users can see why they were flagged (explainable decisions) and their appeals."""
    events = db.execute(
        select(RiskEvent).where(RiskEvent.user_id == user.id).order_by(RiskEvent.id.desc()).limit(50)
    ).scalars().all()
    appeals = db.execute(
        select(Appeal).where(Appeal.user_id == user.id).order_by(Appeal.id.desc()).limit(20)
    ).scalars().all()
    return {
        "risk_level": user.risk_level,
        "status": user.status,
        "verification_status": user.verification_status,
        "events": [{"id": e.id, "event_type": e.event_type, "severity": e.severity, "reason": e.reason,
                    "created_at": e.created_at, "reviewed_at": e.reviewed_at, "resolution": e.resolution}
                   for e in events],
        "appeals": [{"id": a.id, "status": a.status, "message": a.message, "created_at": a.created_at,
                     "resolution": a.resolution} for a in appeals],
    }


@router.post("/appeals", status_code=201)
def create_appeal(body: AppealIn, request: Request, user: User = Depends(require_user),
                  db: Session = Depends(get_db)):
    if body.risk_event_id is not None:
        ev = db.get(RiskEvent, body.risk_event_id)
        if ev is None or ev.user_id != user.id:
            from app.errors import AppError
            raise AppError(404, "RISK_EVENT_NOT_FOUND", "Risk event not found")
    appeal = Appeal(user_id=user.id, risk_event_id=body.risk_event_id, message=body.message)
    db.add(appeal)
    db.flush()
    write_audit(db, "appeal.create", actor_id=user.id, target_type="appeal", target_id=appeal.id,
                request_id=request.state.request_id)
    db.commit()
    return {"id": appeal.id, "status": appeal.status}


@router.post("/reports", status_code=201)
def create_report(body: ReportIn, request: Request, user: User = Depends(require_user),
                  db: Session = Depends(get_db)):
    report = Report(reporter_id=user.id, target_type=body.target_type, target_id=body.target_id,
                    reason=body.reason)
    db.add(report)
    db.flush()
    write_audit(db, "report.create", actor_id=user.id, target_type="report", target_id=report.id,
                request_id=request.state.request_id)
    db.commit()
    return {"id": report.id, "status": report.status}
