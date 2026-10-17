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
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Donation, Trade, User
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


def donate(db: Session, sender: User, recipient_username: str, amount: int, message: str | None,
           request_id: str | None) -> Donation:
    s = get_settings()
    if sender.role != "user" or sender.status != "active":
        raise AppError(403, "ACCOUNT_RESTRICTED", "This account cannot donate")
    if not (s.donation_min_amount <= amount <= s.donation_box_daily_limit):
        raise AppError(422, "INVALID_AMOUNT", f"기부 금액은 {s.donation_min_amount:,}~{s.donation_box_daily_limit:,} 크레딧입니다")
    recipient = db.execute(select(User).where(
        User.normalized_username == recipient_username.strip().lower())).scalar_one_or_none()
    if recipient is None or recipient.role != "user" or recipient.status == "suspended":
        raise AppError(404, "RECIPIENT_NOT_FOUND", "받는 사용자를 찾을 수 없습니다")
    if recipient.id == sender.id:
        raise AppError(422, "SELF_DONATION", "자기 자신에게는 기부할 수 없습니다")
    now = utcnow()
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

    # Serialise per recipient + sender so concurrent gifts cannot overflow the daily box.
    accounts = lock_accounts(db, {sender.id, recipient.id})
    since = _day_start(now)
    if _sum(db, Donation.recipient_id, recipient.id, since) + amount > s.donation_box_daily_limit:
        raise AppError(409, "DONATION_BOX_FULL", "받는 사람의 오늘 기부함 한도(500,000)를 넘습니다")
    if _sum(db, Donation.sender_id, sender.id, since) + amount > s.donation_box_daily_limit:
        raise AppError(409, "DAILY_DONATION_LIMIT", "오늘 보낼 수 있는 기부 한도(500,000)를 넘습니다")
    sa = accounts[sender.id]
    if sa.cash_balance - sa.reserved_cash < amount:
        raise AppError(409, "INSUFFICIENT_FUNDS", "가용 현금이 부족합니다")
    txid = uuid.uuid4()
    msg = _clean(message)
    d = Donation(sender_id=sender.id, recipient_id=recipient.id, amount=amount, message=msg, transaction_id=txid)
    db.add(d)
    db.flush()
    post_entry(db, sa, entry_type="donation_sent", amount=-amount, transaction_id=txid,
               reference_type="donation", reference_id=d.id, memo=f"to {recipient.username}")
    post_entry(db, accounts[recipient.id], entry_type="donation_received", amount=amount, transaction_id=txid,
               reference_type="donation", reference_id=d.id, memo=f"from {sender.username}")
    write_audit(db, "donation.send", actor_id=sender.id, target_type="user", target_id=recipient.id,
                request_id=request_id, metadata={"amount": amount, "donation_id": d.id})
    notify(db, recipient.id, "donation_received", f"🎁 {sender.username}님이 기부했습니다",
           f"{amount:,} 크레딧" + (f" · \"{msg}\"" if msg else ""), "/donations")
    return d


def history(db: Session, user_id: int, limit: int = 50) -> list[dict]:
    rows = db.execute(text(
        "SELECT d.id, d.amount, d.message, d.created_at, s.username AS sender, r.username AS recipient, "
        "d.sender_id = :u AS outgoing FROM donations d JOIN users s ON s.id = d.sender_id "
        "JOIN users r ON r.id = d.recipient_id WHERE d.sender_id = :u OR d.recipient_id = :u "
        "ORDER BY d.id DESC LIMIT :l"), {"u": user_id, "l": limit}).mappings().all()
    return [dict(r) for r in rows]
