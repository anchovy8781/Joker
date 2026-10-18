"""In-app notifications (written in the same transaction as the event they describe)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, Position, User


def notify(db: Session, user_id: int, kind: str, title: str, body: str = "", link: str | None = None) -> None:
    user = db.get(User, user_id)
    if user is None or user.role in ("system", "bot"):  # nobody reads these accounts' inboxes
        return
    db.add(Notification(user_id=user_id, kind=kind, title=title[:200], body=body[:1000], link=link))


def notify_holders(db: Session, asset_id: int, kind: str, title: str, body: str, link: str | None = None) -> int:
    holders = db.execute(
        select(Position.user_id).join(User, User.id == Position.user_id)
        .where(Position.asset_id == asset_id, Position.quantity > 0, User.role == "user")
    ).scalars().all()
    for uid in holders:
        db.add(Notification(user_id=uid, kind=kind, title=title[:200], body=body[:1000], link=link))
    return len(holders)
