"""Price alerts: notify a user once when an asset trades at/above or at/below a target."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Channel, MarketAsset, PriceAlert, User
from app.security import utcnow
from app.services.notify import notify

ACTIVE = (PriceAlert.triggered_at.is_(None), PriceAlert.canceled_at.is_(None))


def create(db: Session, user: User, asset_id: int, direction: str, target_price: int) -> PriceAlert:
    asset = db.execute(select(MarketAsset).join(Channel, Channel.id == MarketAsset.channel_id)
                       .where(MarketAsset.id == asset_id, Channel.listing_status == "listed")).scalar_one_or_none()
    if asset is None:
        raise AppError(404, "ASSET_NOT_FOUND", "Asset not found")
    active = db.execute(select(func.count(PriceAlert.id)).where(PriceAlert.user_id == user.id, *ACTIVE)).scalar_one()
    if active >= get_settings().price_alerts_max_active:
        raise AppError(409, "ALERT_LIMIT", f"가격 알림은 최대 {get_settings().price_alerts_max_active}개까지 걸 수 있어요")
    if (direction == "above" and asset.current_price >= target_price) or \
            (direction == "below" and asset.current_price <= target_price):
        raise AppError(422, "ALREADY_REACHED", "현재가가 이미 그 가격에 도달했어요")
    a = PriceAlert(user_id=user.id, asset_id=asset_id, direction=direction, target_price=target_price)
    db.add(a)
    db.flush()
    return a


def cancel(db: Session, user: User, alert_id: int) -> PriceAlert:
    a = db.get(PriceAlert, alert_id)
    if a is None or a.user_id != user.id:
        raise AppError(404, "NOT_FOUND", "Alert not found")
    if a.triggered_at is None and a.canceled_at is None:
        a.canceled_at = utcnow()
    return a


def out(a: PriceAlert, symbol: str, current: int) -> dict:
    return {"id": a.id, "asset_id": a.asset_id, "symbol": symbol, "direction": a.direction,
            "target_price": a.target_price, "current_price": current, "created_at": a.created_at,
            "triggered_at": a.triggered_at, "active": a.triggered_at is None and a.canceled_at is None}


def mine(db: Session, user: User, asset_id: int | None = None) -> list[dict]:
    q = (select(PriceAlert, MarketAsset.symbol, MarketAsset.current_price)
         .join(MarketAsset, MarketAsset.id == PriceAlert.asset_id)
         .where(PriceAlert.user_id == user.id, PriceAlert.canceled_at.is_(None)))
    if asset_id:
        q = q.where(PriceAlert.asset_id == asset_id)
    return [out(a, sym, cur) for a, sym, cur in db.execute(q.order_by(PriceAlert.id.desc()).limit(50)).all()]


def check_all(db: Session) -> int:
    """Fire every alert whose condition holds at the current price (each fires once)."""
    rows = db.execute(
        select(PriceAlert, MarketAsset.symbol, MarketAsset.current_price)
        .join(MarketAsset, MarketAsset.id == PriceAlert.asset_id)
        .where(*ACTIVE, ((PriceAlert.direction == "above") & (MarketAsset.current_price >= PriceAlert.target_price))
               | ((PriceAlert.direction == "below") & (MarketAsset.current_price <= PriceAlert.target_price)))
        .with_for_update(of=PriceAlert, skip_locked=True)).all()
    now = utcnow()
    for a, symbol, price in rows:
        a.triggered_at = now
        word = "이상" if a.direction == "above" else "이하"
        notify(db, a.user_id, "price_alert", f"🔔 {symbol} {a.target_price:,} {word} 도달",
               f"현재가 {price:,}", f"/assets/{a.asset_id}")
    return len(rows)
