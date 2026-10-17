"""Group chat, notifications, missions and user listing requests."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import get_db, run_in_transaction
from app.models import AutoOrder, Notification, User
from app.schemas import AutoOrderIn, ChatIn, DonationIn, ListingRequestIn, MarkReadIn
from app.security import require_user, utcnow
from app.services import auto_orders, chat, donations, listing_requests, market_hours, missions
from app.services.youtube import get_api_provider

router = APIRouter(prefix="/api", tags=["social"])


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# ---------------------------------------------------------------- chat
@router.get("/chat/{room}/messages")
def chat_messages(room: str, after_id: int = Query(default=0, ge=0),
                  limit: int = Query(default=50, ge=1, le=100), db: Session = Depends(get_db)):
    return chat.list_messages(db, room, after_id, limit)


@router.post("/chat/{room}/messages", status_code=201)
def chat_post(room: str, body: ChatIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    msg = chat.post_message(db, user, room, body.body)
    db.commit()
    return {"id": msg.id, "room": msg.room, "username": user.username, "body": msg.body,
            "created_at": msg.created_at}


# ---------------------------------------------------------------- notifications
@router.get("/notifications")
def notifications(user: User = Depends(require_user), db: Session = Depends(get_db),
                  unread_only: bool = False, limit: int = Query(default=30, ge=1, le=100)):
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    return [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link,
             "created_at": n.created_at, "read": n.read_at is not None}
            for n in db.execute(q.order_by(Notification.id.desc()).limit(limit)).scalars()]


@router.get("/notifications/unread-count")
def unread_count(user: User = Depends(require_user), db: Session = Depends(get_db)):
    n = db.execute(select(func.count(Notification.id)).where(
        Notification.user_id == user.id, Notification.read_at.is_(None))).scalar_one()
    return {"unread": n}


@router.post("/notifications/read")
def mark_read(body: MarkReadIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    # Always scoped to the caller: ids belonging to other users are silently ignored.
    stmt = update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    if not body.all:
        stmt = stmt.where(Notification.id.in_(body.ids or []))
    n = db.execute(stmt.values(read_at=utcnow())).rowcount
    db.commit()
    return {"marked": n}


# ---------------------------------------------------------------- missions
@router.get("/missions")
def list_missions(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return missions.list_missions(db, user)


@router.post("/missions/{code}/claim", status_code=201)
def claim_mission(code: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    uid = user.id
    um = run_in_transaction(db, lambda s: missions.claim(s, s.get(User, uid), code, _rid(request)))
    return {"mission_code": um.mission_code, "period": um.period, "reward": um.reward}


# ---------------------------------------------------------------- listing requests
@router.post("/listing-requests", status_code=201)
def request_listing(body: ListingRequestIn, request: Request, user: User = Depends(require_user),
                    db: Session = Depends(get_db)):
    ch = listing_requests.submit(db, user, body.channel, body.note, get_api_provider(), _rid(request))
    db.commit()
    return {"id": ch.id, "youtube_channel_id": ch.youtube_channel_id, "title": ch.title,
            "listing_status": ch.listing_status}


@router.get("/listing-requests")
def my_listing_requests(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return listing_requests.mine(db, user)


@router.get("/listing-requests/status")
def listing_request_status(db: Session = Depends(get_db)):
    return listing_requests.window_status(db)


# ---------------------------------------------------------------- market hours
@router.get("/market/hours")
def market_hours_status():
    st = market_hours.status(utcnow())
    return {"open": st.open, "enforced": st.enforced, "session_open": st.session_open,
            "session_close": st.session_close, "next_open": st.next_open, "next_close": st.next_close,
            "timezone": "Asia/Seoul", "schedule": market_hours.schedule_table(),
            "note": "장외 시간에는 매수·매도 예약 주문(시장가·지정가)이 가능하며, 장 시작 시 접수 순서대로 제출됩니다."}


# ---------------------------------------------------------------- auto (price-triggered) orders
def _auto_out(a: AutoOrder) -> dict:
    return {"id": a.id, "asset_id": a.asset_id, "side": a.side, "trigger_condition": a.trigger_condition,
            "trigger_price": a.trigger_price, "order_type": a.order_type, "limit_price": a.limit_price,
            "quantity": a.quantity, "status": a.status, "expires_at": a.expires_at, "order_id": a.order_id,
            "failure_reason": a.failure_reason, "created_at": a.created_at, "triggered_at": a.triggered_at}


@router.post("/auto-orders", status_code=201)
def create_auto_order(body: AutoOrderIn, request: Request, user: User = Depends(require_user),
                      db: Session = Depends(get_db)):
    a = auto_orders.create(db, user, asset_id=body.asset_id, side=body.side, trigger_condition=body.trigger_condition,
                           trigger_price=body.trigger_price, order_type=body.order_type,
                           limit_price=body.limit_price, quantity=body.quantity, days=body.days)
    db.commit()
    # It may already be satisfied by the current price.
    auto_orders.evaluate(db, asset_id=body.asset_id)
    db.commit()
    db.refresh(a)
    return _auto_out(a)


@router.get("/auto-orders")
def list_auto_orders(user: User = Depends(require_user), db: Session = Depends(get_db),
                     asset_id: int | None = None):
    q = select(AutoOrder).where(AutoOrder.user_id == user.id)
    if asset_id:
        q = q.where(AutoOrder.asset_id == asset_id)
    return [_auto_out(a) for a in db.execute(q.order_by(AutoOrder.id.desc()).limit(100)).scalars()]


@router.post("/auto-orders/{auto_id}/cancel")
def cancel_auto_order(auto_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    a = auto_orders.cancel(db, user, auto_id)
    db.commit()
    return _auto_out(a)


# ---------------------------------------------------------------- donation box
@router.get("/donations")
def donation_box(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return {"box": donations.box_status(db, user.id), "history": donations.history(db, user.id)}


@router.post("/donations", status_code=201)
def donate(body: DonationIn, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    uid = user.id
    d = run_in_transaction(db, lambda s: donations.donate(s, s.get(User, uid), body.recipient, body.amount,
                                                           body.message, _rid(request)))
    return {"id": d.id, "amount": d.amount, "box": donations.box_status(db, uid)}
