"""Server-side fee and price arithmetic (integer credits only)."""

from dataclasses import dataclass
from datetime import datetime

from app.config import get_settings


def fee_for(notional: int, bps: int | None = None) -> int:
    """Trading fee, rounded up to the next whole credit so splitting orders cannot dodge it."""
    rate = get_settings().fee_bps if bps is None else bps
    return (notional * rate + 9_999) // 10_000


def per_unit_fee_ceiling(price: int, bps: int | None = None) -> int:
    """Upper bound of the fee charged per share at `price`, for any way a fill is split.

    ceil(q * p * r) <= q * ceil(p * r) for positive integers q, so reserving
    q * (p + ceil(p * r)) always covers the cash an order can consume.
    """
    return fee_for(price, bps)


def buy_reservation(quantity: int, max_price: int, bps: int | None = None) -> int:
    return quantity * (max_price + per_unit_fee_ceiling(max_price, bps))


def bps_up(price: int, bps: int) -> int:
    """price * (1 + bps), floored."""
    return price * (10_000 + bps) // 10_000


def bps_down(price: int, bps: int) -> int:
    """price * (1 - bps), rounded up so the band never widens."""
    return -(-price * (10_000 - bps) // 10_000)


@dataclass
class FeePolicy:
    bps: int
    surcharge: bool
    reason: str | None  # 'new_video' | 'celebrity_new_channel'
    until: datetime | None


def fee_policy(asset, now: datetime) -> FeePolicy:
    """Fee rate that applies to an order accepted on `asset` at `now`."""
    s = get_settings()
    windows = [
        (asset.new_video_until, "new_video"),
        (asset.celebrity_until, "celebrity_new_channel"),
    ]
    active = [(until, reason) for until, reason in windows if until is not None and until > now]
    if not active:
        return FeePolicy(s.fee_bps, False, None, None)
    until, reason = max(active)
    return FeePolicy(max(s.surcharge_fee_bps, s.fee_bps), True, reason, until)
