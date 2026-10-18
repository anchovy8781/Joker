import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import order_out
from app.config import get_settings
from app.db import get_db, run_in_transaction, session_factory
from app.errors import AppError
from app.models import Order, Trade, User
from app.schemas import OrderAmendIn, OrderIn, OrderOut, PlaceOrderOut, TradeOut
from app.security import hit_rate_limit, require_user
from app.services import orders as engine
from app.services import auto_orders, risk

router = APIRouter(prefix="/api/orders", tags=["orders"])
log = logging.getLogger("yex.api.orders")


def _after_trades(trades: list, asset_id: int) -> None:
    """Abuse checks and auto-order triggers run after the trade commits, in their own
    transaction; failures are logged only."""
    rdb = session_factory()()
    try:
        risk.check_trades(rdb, trades)
        auto_orders.evaluate(rdb, asset_id=asset_id)
        rdb.commit()
    except Exception:
        rdb.rollback()
        log.exception("post-trade risk check failed")
    finally:
        rdb.close()


def _fills_of(db: Session, order_id: int) -> list[TradeOut]:
    return [TradeOut.model_validate(t) for t in db.execute(
        select(Trade).where((Trade.buy_order_id == order_id) | (Trade.sell_order_id == order_id))
        .order_by(Trade.id)).scalars()]


@router.post("", response_model=PlaceOrderOut)
def place(body: OrderIn, response: Response, user: User = Depends(require_user),
          db: Session = Depends(get_db)):
    s = get_settings()
    limit = s.rl_orders_per_user_medium_risk if user.risk_level == "medium" else s.rl_orders_per_user
    hit_rate_limit(f"orders:user:{user.id}", *limit)
    req = engine.OrderRequest(asset_id=body.asset_id, side=body.side, order_type=body.order_type,
                              quantity=body.quantity, limit_price=body.limit_price,
                              idempotency_key=body.idempotency_key)
    user_id = user.id

    def tx(session: Session):
        u = session.get(User, user_id)
        return engine.place_order(session, u, req)

    result = run_in_transaction(db, tx)
    fills = [TradeOut.model_validate(t) for t in result.trades]
    if result.replayed:
        fills = [TradeOut.model_validate(t) for t in db.execute(
            select(Trade).where((Trade.buy_order_id == result.order.id) | (Trade.sell_order_id == result.order.id))
            .order_by(Trade.id)).scalars()]
    elif result.trades:
        _after_trades(result.trades, body.asset_id)
    response.status_code = 200 if result.replayed else 201
    return PlaceOrderOut(order=order_out(result.order), fills=fills, replayed=result.replayed)


@router.get("", response_model=list[OrderOut])
def list_orders(user: User = Depends(require_user), db: Session = Depends(get_db),
                status: Literal["scheduled", "open", "partially_filled", "filled", "canceled", "rejected", "active"] | None = None,
                asset_id: int | None = None, limit: int = Query(default=100, ge=1, le=500)):
    q = select(Order).where(Order.user_id == user.id)
    if status == "active":
        q = q.where(Order.status.in_(engine.RESERVING))
    elif status:
        q = q.where(Order.status == status)
    if asset_id:
        q = q.where(Order.asset_id == asset_id)
    return [order_out(o) for o in db.execute(q.order_by(Order.id.desc()).limit(limit)).scalars()]


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if o is None or o.user_id != user.id:
        raise AppError(404, "ORDER_NOT_FOUND", "Order not found")
    return order_out(o)


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel(order_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    user_id = user.id
    order = run_in_transaction(db, lambda s: engine.cancel_order(s, s.get(User, user_id), order_id))
    return order_out(order)


@router.post("/{order_id}/amend", response_model=PlaceOrderOut)
def amend(order_id: int, body: OrderAmendIn, response: Response, user: User = Depends(require_user),
          db: Session = Depends(get_db)):
    """매매 정정: replace a resting limit order's price and/or quantity in one step. The old
    order's remainder is cancelled (reservation released) and a new order is placed in the
    same transaction — like a real exchange, the new order joins the back of the queue at its
    price, and it can fill immediately if the new price crosses the book."""
    s = get_settings()
    hit_rate_limit(f"orders:user:{user.id}", *s.rl_orders_per_user)
    user_id = user.id

    def tx(session: Session):
        u = session.get(User, user_id)
        old = session.get(Order, order_id)
        if old is None or old.user_id != user_id:
            raise AppError(404, "ORDER_NOT_FOUND", "Order not found")
        req = engine.OrderRequest(asset_id=old.asset_id, side=old.side, order_type="limit",
                                  quantity=body.quantity or old.remaining_quantity,
                                  limit_price=body.limit_price or old.limit_price,
                                  idempotency_key=body.idempotency_key)
        done = session.execute(select(Order).where(Order.user_id == user_id,
                                                   Order.idempotency_key == body.idempotency_key)).scalar_one_or_none()
        if done is not None:  # a retry: only the same amendment of this order counts as one
            if done.request_hash != req.digest() or old.status != "canceled" or old.status_reason != "USER_AMENDED":
                raise AppError(409, "IDEMPOTENCY_CONFLICT", "이 요청 키는 이미 다른 주문에 쓰였어요")
            return engine.PlaceResult(order=done, replayed=True)
        if old.order_type != "limit":
            raise AppError(409, "NOT_AMENDABLE", "지정가 주문만 정정할 수 있어요")
        if body.limit_price is None and body.quantity is None:
            raise AppError(422, "NOTHING_TO_CHANGE", "바꿀 가격이나 수량을 입력해 주세요")
        engine.cancel_order(session, u, order_id, reason="USER_AMENDED")
        return engine.place_order(session, u, req)

    result = run_in_transaction(db, tx)
    response.status_code = 200
    if result.replayed:
        fills = _fills_of(db, result.order.id)
    else:
        fills = [TradeOut.model_validate(t) for t in result.trades]
        if result.trades:  # amending into the book fills it: same checks as a new order
            _after_trades(result.trades, result.order.asset_id)
    return PlaceOrderOut(order=order_out(result.order), fills=fills, replayed=result.replayed)
