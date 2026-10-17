"""Server-side fee and price arithmetic (integer credits only)."""

from app.config import get_settings


def fee_for(notional: int) -> int:
    """Trading fee, rounded up to the next whole credit so splitting orders cannot dodge it."""
    bps = get_settings().fee_bps
    return (notional * bps + 9_999) // 10_000


def per_unit_fee_ceiling(price: int) -> int:
    """Upper bound of the fee charged per share at `price`, for any way a fill is split.

    ceil(q * p * r) <= q * ceil(p * r) for positive integers q, so reserving
    q * (p + ceil(p * r)) always covers the cash an order can consume.
    """
    return fee_for(price)


def buy_reservation(quantity: int, max_price: int) -> int:
    return quantity * (max_price + per_unit_fee_ceiling(max_price))


def bps_up(price: int, bps: int) -> int:
    """price * (1 + bps), floored."""
    return price * (10_000 + bps) // 10_000


def bps_down(price: int, bps: int) -> int:
    """price * (1 - bps), rounded up so the band never widens."""
    return -(-price * (10_000 - bps) // 10_000)
