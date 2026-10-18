"""Player progression: experience levels and asset tiers (computed, nothing stored).

XP comes from things a player does — every fill, every 100,000 credits traded, every
mission completed — so it only goes up. The tier follows total equity (cash + holdings)
and can go up or down with the market.
"""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Trade, UserMission

TIERS = [  # (minimum total equity, name)
    (0, "브론즈"), (1_200_000, "실버"), (2_000_000, "골드"), (5_000_000, "플래티넘"),
    (20_000_000, "다이아"), (100_000_000, "마스터"),
]


def level_for(xp: int) -> tuple[int, int, int]:
    """(level, xp where this level starts, xp where the next one starts). Level n needs
    50·n·(n−1) XP in total: 0, 100, 300, 600, 1000, …"""
    level = 1
    while 50 * (level + 1) * level <= xp:
        level += 1
    return level, 50 * level * (level - 1), 50 * (level + 1) * level


def tier_for(equity: int) -> tuple[str, int | None]:
    """(tier name, equity needed for the next tier or None at the top)."""
    name, nxt = TIERS[0][1], None
    for i, (floor, n) in enumerate(TIERS):
        if equity >= floor:
            name = n
            nxt = TIERS[i + 1][0] if i + 1 < len(TIERS) else None
    return name, nxt


def progress(db: Session, user_id: int, equity: int) -> dict:
    trades, value = db.execute(select(func.count(Trade.id), func.coalesce(func.sum(Trade.quantity * Trade.execution_price), 0))
                               .where(or_(Trade.buyer_id == user_id, Trade.seller_id == user_id))).one()
    missions = db.execute(select(func.count(UserMission.id)).where(UserMission.user_id == user_id)).scalar_one()
    xp = int(trades) * 10 + int(value) // 100_000 * 5 + int(missions) * 50
    level, start, nxt = level_for(xp)
    tier, next_tier_at = tier_for(equity)
    return {"xp": xp, "level": level, "level_start_xp": start, "next_level_xp": nxt, "tier": tier,
            "next_tier_equity": next_tier_at}
