"""기부함: user-to-user gifts of virtual credits.

A donation is a zero-sum ledger transfer (donation_sent / donation_received). Limits
keep it from becoming a way to funnel sign-up grants from throw-away accounts:
  * each user can receive at most `donation_box_daily_limit` (500,000) per KST day
    (the box is full after that) and send at most the same amount per day,
  * senders need an account older than 72h with at least 5 trades,
  * accounts linked by network cannot donate to each other (logged as a risk event).
"""

import re
import unicodedata
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Donation, DonationBox, Trade, User
from app.security import utcnow
from app.services import risk
from app.services.audit import write_audit
from app.services.ledger import lock_accounts, post_entry
from app.services.notify import notify

KST = ZoneInfo("Asia/Seoul")


def _day_start(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(KST).date(), time(), KST)


def _sum(db: Session, column, user_id: int, since: datetime) -> int:
    return int(db.execute(select(func.coalesce(func.sum(Donation.amount), 0)).where(
        column == user_id, Donation.created_at >= since)).scalar_one())


def box_status(db: Session, user_id: int) -> dict:
    s = get_settings()
    since = _day_start(utcnow())
    received = _sum(db, Donation.recipient_id, user_id, since)
    sent = _sum(db, Donation.sender_id, user_id, since)
    return {"daily_limit": s.donation_box_daily_limit, "received_today": received, "sent_today": sent,
            "receive_remaining": max(0, s.donation_box_daily_limit - received),
            "send_remaining": max(0, s.donation_box_daily_limit - sent), "full": received >= s.donation_box_daily_limit}


def _clean(msg: str | None) -> str | None:
    if not msg:
        return None
    t = "".join(ch for ch in msg if unicodedata.category(ch)[0] != "C")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:100] or None


def _check_sender(db: Session, sender: User, recipient: User, amount: int, now: datetime) -> None:
    s = get_settings()
    if sender.role != "user" or sender.status != "active":
        raise AppError(403, "ACCOUNT_RESTRICTED", "This account cannot donate")
    if not (s.donation_min_amount <= amount <= s.donation_box_daily_limit):
        raise AppError(422, "INVALID_AMOUNT", f"기부 금액은 {s.donation_min_amount:,}~{s.donation_box_daily_limit:,} 크레딧입니다")
    if recipient.role != "user" or recipient.status == "suspended":
        raise AppError(404, "RECIPIENT_NOT_FOUND", "받는 사용자를 찾을 수 없습니다")
    if recipient.id == sender.id:
        raise AppError(422, "SELF_DONATION", "자기 자신에게는 기부할 수 없습니다")
    if now - sender.created_at < timedelta(hours=s.donation_min_account_age_hours):
        raise AppError(403, "ACCOUNT_TOO_NEW", f"가입 {s.donation_min_account_age_hours}시간 이후부터 기부할 수 있습니다")
    trades = db.execute(select(func.count(Trade.id)).where(
        or_(Trade.buyer_id == sender.id, Trade.seller_id == sender.id))).scalar_one()
    if trades < s.donation_min_trades:
        raise AppError(403, "NOT_ENOUGH_ACTIVITY", f"체결 {s.donation_min_trades}회 이상부터 기부할 수 있습니다")
    links = risk.related(db, sender.id, recipient.id)
    if links:
        risk.add_event(db, user_id=sender.id, asset_id=None, event_type="related_account_donation", severity="low",
                       reason=f"Donation attempt to a linked account ({', '.join(links)}) was blocked",
                       evidence={"recipient_id": recipient.id, "amount": amount, "links": links},
                       dedup_key=f"reldon:{sender.id}:{recipient.id}:{now.date()}")
        raise AppError(403, "LINKED_ACCOUNTS", "같은 네트워크에서 만든 계정 간에는 기부할 수 없습니다")


def _transfer(db: Session, sender: User, recipient: User, amount: int, message: str | None,
              request_id: str | None, box: DonationBox | None = None) -> Donation:
    """Validated, zero-sum ledger transfer under account locks (shared by direct gifts,
    chat tips and donation boxes, so every path has the same limits)."""
    s = get_settings()
    now = utcnow()
    _check_sender(db, sender, recipient, amount, now)
    # Serialise per recipient + sender so concurrent gifts cannot overflow the daily box.
    accounts = lock_accounts(db, {sender.id, recipient.id})
    since = _day_start(now)
    if _sum(db, Donation.recipient_id, recipient.id, since) + amount > s.donation_box_daily_limit:
        raise AppError(409, "DONATION_BOX_FULL", f"받는 사람의 오늘 기부 한도({s.donation_box_daily_limit:,})를 넘습니다")
    if _sum(db, Donation.sender_id, sender.id, since) + amount > s.donation_box_daily_limit:
        raise AppError(409, "DAILY_DONATION_LIMIT", f"오늘 보낼 수 있는 기부 한도({s.donation_box_daily_limit:,})를 넘습니다")
    sa = accounts[sender.id]
    if sa.cash_balance - sa.reserved_cash < amount:
        raise AppError(409, "INSUFFICIENT_FUNDS", "가용 현금이 부족합니다")
    txid = uuid.uuid4()
    msg = _clean(message)
    d = Donation(sender_id=sender.id, recipient_id=recipient.id, amount=amount, message=msg, transaction_id=txid,
                 box_id=box.id if box else None)
    db.add(d)
    db.flush()
    post_entry(db, sa, entry_type="donation_sent", amount=-amount, transaction_id=txid,
               reference_type="donation", reference_id=d.id, memo=f"to {recipient.username}")
    post_entry(db, accounts[recipient.id], entry_type="donation_received", amount=amount, transaction_id=txid,
               reference_type="donation", reference_id=d.id, memo=f"from {sender.username}")
    write_audit(db, "donation.send", actor_id=sender.id, target_type="user", target_id=recipient.id,
                request_id=request_id, metadata={"amount": amount, "donation_id": d.id,
                                                 "box_id": box.id if box else None})
    where = f" (기부함 '{box.title}')" if box else ""
    notify(db, recipient.id, "donation_received", f"🎁 {sender.username}님이 기부했습니다{where}",
           f"{amount:,} 크레딧" + (f" · \"{msg}\"" if msg else ""), "/donations")
    return d


def _user(db: Session, username: str) -> User:
    u = db.execute(select(User).where(User.normalized_username == username.strip().lstrip("@").lower())).scalar_one_or_none()
    if u is None:
        raise AppError(404, "RECIPIENT_NOT_FOUND", "받는 사용자를 찾을 수 없습니다")
    return u


def donate(db: Session, sender: User, recipient_username: str, amount: int, message: str | None,
           request_id: str | None) -> Donation:
    return _transfer(db, sender, _user(db, recipient_username), amount, message, request_id)


# ---------------------------------------------------------------- chat donation boxes

def box_state(box: DonationBox, now: datetime | None = None) -> str:
    now = now or utcnow()
    if box.status == "closed" or box.raised >= box.goal:
        return "closed"
    return "expired" if now >= box.closes_at else "open"


def box_out(box: DonationBox, owner_name: str, now: datetime | None = None) -> dict:
    return {"id": box.id, "room": box.room, "title": box.title, "goal": box.goal, "raised": box.raised,
            "owner": owner_name, "state": box_state(box, now), "created_at": box.created_at,
            "closes_at": box.closes_at}


def open_box(db: Session, owner: User, room: str, title: str, goal: int, request_id: str | None) -> DonationBox:
    """Open a fundraiser in a chat room. At most one per user per KST day (DB-enforced)."""
    from app.services import chat  # chat imports nothing from here; avoid a cycle at import time

    s = get_settings()
    chat.validate_room(db, room)
    if owner.role != "user" or owner.status != "active":
        raise AppError(403, "ACCOUNT_RESTRICTED", "This account cannot open a donation box")
    if not (s.donation_box_min_goal <= goal <= s.donation_box_daily_limit):
        raise AppError(422, "INVALID_GOAL",
                       f"기부함 목표 금액은 {s.donation_box_min_goal:,}~{s.donation_box_daily_limit:,} 크레딧입니다")
    clean = _clean(title)
    if not clean or len(clean) < 2:
        raise AppError(422, "INVALID_TITLE", "기부함 제목을 2자 이상 입력하세요")
    now = utcnow()
    day = now.astimezone(KST).date()
    if db.execute(select(DonationBox.id).where(DonationBox.owner_id == owner.id, DonationBox.kst_date == day)).first():
        raise AppError(409, "DONATION_BOX_DAILY_LIMIT", "기부함은 하루에 1번만 만들 수 있어요 (내일 다시 만들 수 있어요)")
    box = DonationBox(owner_id=owner.id, room=room, title=clean[:60], goal=goal, raised=0, status="open",
                      kst_date=day, closes_at=now + timedelta(hours=s.donation_box_hours))
    try:
        with db.begin_nested():
            db.add(box)
            db.flush()
    except IntegrityError:
        raise AppError(409, "DONATION_BOX_DAILY_LIMIT", "기부함은 하루에 1번만 만들 수 있어요 (내일 다시 만들 수 있어요)")
    chat.post_event(db, owner, room, "donation_box", box.id, f"🎁 기부함: {box.title} (목표 {goal:,})")
    write_audit(db, "donation_box.open", actor_id=owner.id, target_type="donation_box", target_id=box.id,
                request_id=request_id, metadata={"room": room, "goal": goal})
    return box


def give_to_box(db: Session, sender: User, box_id: int, amount: int, message: str | None,
                request_id: str | None) -> tuple[Donation, DonationBox]:
    from app.services import chat

    db.flush()
    box = db.execute(select(DonationBox).where(DonationBox.id == box_id).with_for_update()
                     .execution_options(populate_existing=True)).scalar_one_or_none()
    if box is None:
        raise AppError(404, "DONATION_BOX_NOT_FOUND", "기부함을 찾을 수 없습니다")
    state = box_state(box)
    if state != "open":
        raise AppError(409, "DONATION_BOX_CLOSED", "마감된 기부함이에요" if state == "closed" else "기간이 끝난 기부함이에요")
    left = box.goal - box.raised
    if amount > left:
        raise AppError(409, "DONATION_BOX_GOAL", f"목표까지 {left:,} 크레딧 남았어요", {"remaining": left})
    owner = db.get(User, box.owner_id)
    d = _transfer(db, sender, owner, amount, message, request_id, box)
    box.raised += amount
    if box.raised >= box.goal:
        box.status = "closed"
        notify(db, owner.id, "donation_box_full", "🎉 기부함 목표 달성", f"'{box.title}' {box.goal:,} 크레딧이 모였어요",
               "/donations")
    chat.post_event(db, sender, box.room, "donation", d.id,
                    f"💝 {owner.username}님의 기부함에 {amount:,} 기부" + (f" · {d.message}" if d.message else ""))
    return d, box


def close_box(db: Session, owner: User, box_id: int) -> DonationBox:
    box = db.get(DonationBox, box_id)
    if box is None or box.owner_id != owner.id:
        raise AppError(404, "DONATION_BOX_NOT_FOUND", "기부함을 찾을 수 없습니다")
    box.status = "closed"
    return box


def my_boxes(db: Session, user: User, limit: int = 10) -> dict:
    now = utcnow()
    rows = db.execute(select(DonationBox).where(DonationBox.owner_id == user.id)
                      .order_by(DonationBox.id.desc()).limit(limit)).scalars().all()
    today = now.astimezone(KST).date()
    return {"can_open_today": not any(b.kst_date == today for b in rows),
            "boxes": [box_out(b, user.username, now) for b in rows]}


def tip(db: Session, sender: User, room: str, recipient_username: str, amount: int, message: str | None,
        request_id: str | None) -> Donation:
    """Gift credits to someone straight from a chat room; the gift is announced in the room."""
    from app.services import chat

    chat.validate_room(db, room)
    recipient = _user(db, recipient_username)
    d = _transfer(db, sender, recipient, amount, message, request_id)
    chat.post_event(db, sender, room, "donation", d.id,
                    f"💝 {recipient.username}님에게 {amount:,} 선물" + (f" · {d.message}" if d.message else ""))
    return d


def history(db: Session, user_id: int, limit: int = 50) -> list[dict]:
    rows = db.execute(text(
        "SELECT d.id, d.amount, d.message, d.created_at, s.username AS sender, r.username AS recipient, "
        "d.sender_id = :u AS outgoing FROM donations d JOIN users s ON s.id = d.sender_id "
        "JOIN users r ON r.id = d.recipient_id WHERE d.sender_id = :u OR d.recipient_id = :u "
        "ORDER BY d.id DESC LIMIT :l"), {"u": user_id, "l": limit}).mappings().all()
    return [dict(r) for r in rows]
