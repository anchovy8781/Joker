"""Missions: server-verified goals that pay a fixed credit reward into the ledger.

Rewards are a deliberate, bounded credit source (one-time missions + one small daily
mission). Each payout is a `mission_reward` ledger entry and a `user_missions` row whose
UNIQUE(user_id, mission_code, period) constraint makes double payment impossible.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Channel, ChatMessage, Order, Position, Trade, User, UserMission
from app.security import utcnow
from app.services.audit import write_audit
from app.services.ledger import lock_account, post_entry
from app.services.notify import notify

KST = ZoneInfo("Asia/Seoul")  # daily missions reset at midnight Korean time


def today_kst(now: datetime | None = None) -> str:
    return (now or utcnow()).astimezone(KST).date().isoformat()


def _day_window(now: datetime) -> tuple[datetime, datetime]:
    d = now.astimezone(KST).date()
    start = datetime.combine(d, time(), KST)
    return start, start + timedelta(days=1)


def _trade_count(db, uid, since=None, until=None):
    q = select(func.count(Trade.id)).where(or_(Trade.buyer_id == uid, Trade.seller_id == uid))
    if since is not None:
        q = q.where(Trade.executed_at >= since, Trade.executed_at < until)
    return db.execute(q).scalar_one()


def _limit_fills(db, uid):
    return db.execute(select(func.count(Order.id)).where(
        Order.user_id == uid, Order.order_type == "limit", Order.remaining_quantity < Order.quantity)).scalar_one()


def _holdings(db, uid):
    return db.execute(select(func.count()).select_from(Position).where(
        Position.user_id == uid, Position.quantity > 0)).scalar_one()


def _profitable_sells(db, uid):
    return db.execute(select(func.count(Trade.id)).where(
        Trade.seller_id == uid, Trade.seller_realized_pnl > 0)).scalar_one()


def _chat_messages(db, uid):
    return db.execute(select(func.count(ChatMessage.id)).where(ChatMessage.user_id == uid)).scalar_one()


def _approved_requests(db, uid):
    return db.execute(select(func.count(Channel.id)).where(
        Channel.requested_by == uid, Channel.listing_status == "listed")).scalar_one()


def _daily_trades(db, uid):
    start, end = _day_window(utcnow())
    return _trade_count(db, uid, start, end)


@dataclass(frozen=True)
class Mission:
    code: str
    title: str
    description: str
    reward: int
    period: str  # 'once' | 'daily'
    target: int
    progress: Callable[[Session, int], int]


MISSIONS: list[Mission] = [
    Mission("first_trade", "첫 거래", "아무 종목이나 1회 체결하기", 20_000, "once", 1,
            lambda db, u: _trade_count(db, u)),
    Mission("first_limit_fill", "지정가 마스터", "지정가 주문을 체결시키기", 10_000, "once", 1, _limit_fills),
    Mission("diversify_3", "분산 투자", "서로 다른 3개 종목을 동시에 보유하기", 30_000, "once", 3, _holdings),
    Mission("first_profit", "첫 수익 실현", "이익을 남기고 매도하기", 20_000, "once", 1, _profitable_sells),
    Mission("first_chat", "인사하기", "단체 채팅에 첫 메시지 남기기", 5_000, "once", 1, _chat_messages),
    Mission("listing_scout", "상장 스카우터", "내가 신청한 채널이 상장 승인되기", 30_000, "once", 1, _approved_requests),
    Mission("daily_trade", "오늘의 거래", "오늘(한국 시간) 1회 이상 체결하기", 3_000, "daily", 1, _daily_trades),
]
BY_CODE = {m.code: m for m in MISSIONS}


def period_for(m: Mission, now: datetime | None = None) -> str:
    return "once" if m.period == "once" else today_kst(now)


def list_missions(db: Session, user: User) -> list[dict]:
    claimed = {(r.mission_code, r.period) for r in db.execute(
        select(UserMission).where(UserMission.user_id == user.id)).scalars()}
    out = []
    for m in MISSIONS:
        period = period_for(m)
        done = (m.code, period) in claimed
        progress = min(m.progress(db, user.id), m.target)
        out.append({"code": m.code, "title": m.title, "description": m.description, "reward": m.reward,
                    "period": m.period, "target": m.target, "progress": progress, "claimed": done,
                    "claimable": not done and progress >= m.target})
    return out


def claim(db: Session, user: User, code: str, request_id: str | None = None) -> UserMission:
    m = BY_CODE.get(code)
    if m is None:
        raise AppError(404, "MISSION_NOT_FOUND", "Mission not found")
    if user.role != "user" or user.status != "active":
        raise AppError(403, "ACCOUNT_RESTRICTED", "This account cannot claim mission rewards")
    account = lock_account(db, user.id)  # serialises concurrent claims by the same user
    period = period_for(m)
    if db.execute(select(UserMission.id).where(UserMission.user_id == user.id, UserMission.mission_code == code,
                                               UserMission.period == period)).first():
        raise AppError(409, "MISSION_ALREADY_CLAIMED", "Reward already received")
    if m.progress(db, user.id) < m.target:
        raise AppError(409, "MISSION_NOT_COMPLETED", "Mission is not completed yet")
    try:
        with db.begin_nested():
            entry = post_entry(db, account, entry_type="mission_reward", amount=m.reward,
                               transaction_id=uuid.uuid4(), reference_type="mission",
                               reference_id=f"{code}:{period}", memo=m.title)
            db.flush()
            um = UserMission(user_id=user.id, mission_code=code, period=period, reward=m.reward,
                             ledger_entry_id=entry.id)
            db.add(um)
            db.flush()
    except IntegrityError:
        db.refresh(account)
        raise AppError(409, "MISSION_ALREADY_CLAIMED", "Reward already received")
    write_audit(db, "mission.claim", actor_id=user.id, target_type="mission", target_id=code,
                request_id=request_id, metadata={"period": period, "reward": m.reward})
    notify(db, user.id, "mission_reward", f"미션 완료: {m.title}",
           f"{m.reward:,} 크레딧이 지급되었습니다.", "/missions")
    return um
