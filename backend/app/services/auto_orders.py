"""지정가 자동 매수/매도: price-triggered orders.

When an asset's market price reaches the trigger (<= or >=), a normal order is submitted
through the regular engine (same validation, reservation, fees, price-time priority).
Nothing is reserved before the trigger fires; if funds/shares are short at that moment
the auto order fails and the user is notified.
"""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import AutoOrder, MarketAsset, User
from app.security import utcnow
from app.services.notify import notify
from app.services.orders import OrderRequest, place_order


def create(db: Session, user: User, *, asset_id: int, side: str, trigger_condition: str, trigger_price: int,
           order_type: str, limit_price: int | None, quantity: int, days: int) -> AutoOrder:
    s = get_settings()
    if user.status != "active":
        raise AppError(403, "ACCOUNT_RESTRICTED", "Trading is restricted for this account")
    if db.get(MarketAsset, asset_id) is None:
        raise AppError(404, "ASSET_NOT_FOUND", "Asset not found")
    if (order_type == "limit") != (limit_price is not None):
        raise AppError(422, "INVALID_PRICE", "limit_price is required for limit auto orders only")
    if not (1 <= days <= s.auto_orders_max_days):
        raise AppError(422, "INVALID_EXPIRY", f"Expiry must be 1..{s.auto_orders_max_days} days")
    active = db.execute(select(func.count(AutoOrder.id)).where(
        AutoOrder.user_id == user.id, AutoOrder.status == "active")).scalar_one()
    if active >= s.auto_orders_max_active:
        raise AppError(409, "TOO_MANY_AUTO_ORDERS", f"At most {s.auto_orders_max_active} active auto orders")
    ao = AutoOrder(user_id=user.id, asset_id=asset_id, side=side, trigger_condition=trigger_condition,
                   trigger_price=trigger_price, order_type=order_type, limit_price=limit_price,
                   quantity=quantity, expires_at=utcnow() + timedelta(days=days))
    db.add(ao)
    db.flush()
    return ao


def cancel(db: Session, user: User, auto_id: int) -> AutoOrder:
    ao = db.execute(select(AutoOrder).where(AutoOrder.id == auto_id).with_for_update()).scalar_one_or_none()
    if ao is None or ao.user_id != user.id:
        raise AppError(404, "AUTO_ORDER_NOT_FOUND", "Auto order not found")
    if ao.status != "active":
        raise AppError(409, "AUTO_ORDER_NOT_ACTIVE", f"Auto order is already {ao.status}")
    ao.status = "canceled"
    return ao


def _triggered(ao: AutoOrder, price: int) -> bool:
    return price <= ao.trigger_price if ao.trigger_condition == "lte" else price >= ao.trigger_price


def evaluate(db: Session, asset_id: int | None = None, max_rounds: int = 5) -> dict:
    """Fire every active auto order whose trigger is met. Each firing is isolated in a
    savepoint; repeats a few rounds because fills can move the price and trigger more."""
    now = utcnow()
    fired = failed = expired = 0
    for _ in range(max_rounds):
        q = (select(AutoOrder, MarketAsset.current_price, MarketAsset.symbol)
             .join(MarketAsset, MarketAsset.id == AutoOrder.asset_id)
             .where(AutoOrder.status == "active").order_by(AutoOrder.id))
        if asset_id is not None:
            q = q.where(AutoOrder.asset_id == asset_id)
        progressed = False
        for ao, price, symbol in db.execute(q).all():
            if ao.expires_at <= now:
                ao.status = "expired"
                expired += 1
                continue
            if not _triggered(ao, price):
                continue
            progressed = True
            ao.triggered_at = now
            user = db.get(User, ao.user_id)
            req = OrderRequest(ao.asset_id, ao.side, ao.order_type, ao.quantity, ao.limit_price, f"auto:{ao.id}")
            try:
                with db.begin_nested():
                    result = place_order(db, user, req)
                ao.status, ao.order_id = "triggered", result.order.id
                fired += 1
                notify(db, ao.user_id, "auto_order_triggered", f"{symbol} 자동 {'매수' if ao.side == 'buy' else '매도'} 실행",
                       f"현재가 {price:,}이(가) 조건 {ao.trigger_price:,}에 도달해 주문 #{result.order.id}을(를) 제출했습니다.",
                       f"/assets/{ao.asset_id}")
            except AppError as exc:
                ao.status, ao.failure_reason = "failed", f"{exc.code}: {exc.message}"[:200]
                failed += 1
                notify(db, ao.user_id, "auto_order_failed", f"{symbol} 자동 주문 실패", exc.message, f"/assets/{ao.asset_id}")
        db.flush()
        if not progressed:
            break
    return {"fired": fired, "failed": failed, "expired": expired}
