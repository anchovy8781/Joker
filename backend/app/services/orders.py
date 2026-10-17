"""Order placement, matching and cancellation.

Locking protocol (always in this order, inside one DB transaction):
    1. market_assets row (FOR UPDATE)  -> serialises all book/position changes for that asset
    2. resting orders on the book       (FOR UPDATE)
    3. accounts of every participant    (FOR UPDATE, ascending user_id)
Positions are only modified while the asset row is locked, so they need no extra lock.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Account, Channel, MarketAsset, Order, Position, Trade, User
from app.security import utcnow
from app.services import bars, market_hours
from app.services.fees import bps_down, bps_up, buy_reservation, fee_for, fee_policy
from app.services.ledger import lock_accounts, post_entry
from app.services.notify import notify

RESTING = ("open", "partially_filled")  # on the book
RESERVING = RESTING + ("scheduled",)  # holding a cash/share reservation
MAX_MATCH_DEPTH = 500  # resting orders examined (and locked) per incoming order


@dataclass
class OrderRequest:
    asset_id: int
    side: str  # buy | sell
    order_type: str  # market | limit
    quantity: int
    limit_price: int | None
    idempotency_key: str

    def digest(self) -> str:
        canonical = json.dumps(
            [self.asset_id, self.side, self.order_type, self.quantity, self.limit_price],
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class PlaceResult:
    order: Order
    trades: list[Trade] = field(default_factory=list)
    replayed: bool = False


def lock_asset(db: Session, asset_id: int) -> MarketAsset:
    asset = db.execute(
        select(MarketAsset).where(MarketAsset.id == asset_id).with_for_update()
    ).scalar_one_or_none()
    if asset is None:
        raise AppError(404, "ASSET_NOT_FOUND", "Asset not found")
    return asset


def _get_position(db: Session, user_id: int, asset_id: int, create: bool) -> Position | None:
    pos = db.get(Position, (user_id, asset_id))
    if pos is None and create:
        pos = Position(
            user_id=user_id,
            asset_id=asset_id,
            quantity=0,
            reserved_quantity=0,
            average_cost=Decimal(0),
            realized_pnl=Decimal(0),
        )
        db.add(pos)
        db.flush()
    return pos


def price_band(db: Session, asset: MarketAsset) -> int:
    """Allowed deviation (bps) of limit prices from the reference price.
    Illiquid assets get a tighter band so thin books are harder to push around."""
    s = get_settings()
    since = utcnow() - timedelta(hours=24)
    trades_24h = db.execute(
        select(func.count(Trade.id)).where(Trade.asset_id == asset.id, Trade.executed_at >= since)
    ).scalar_one()
    return s.low_liquidity_band_bps if trades_24h < s.low_liquidity_daily_trades else s.limit_band_bps


def _validate_shape(req: OrderRequest) -> None:
    s = get_settings()
    if req.side not in ("buy", "sell"):
        raise AppError(422, "INVALID_SIDE", "side must be buy or sell")
    if req.order_type not in ("market", "limit"):
        raise AppError(422, "INVALID_ORDER_TYPE", "order_type must be market or limit")
    if not (0 < req.quantity <= s.max_order_quantity):
        raise AppError(422, "INVALID_QUANTITY", f"quantity must be 1..{s.max_order_quantity}")
    if req.order_type == "limit":
        if req.limit_price is None or req.limit_price <= 0:
            raise AppError(422, "INVALID_PRICE", "limit orders require a positive limit_price")
    elif req.limit_price is not None:
        raise AppError(422, "INVALID_PRICE", "market orders must not specify a price")


def _find_existing(db: Session, user_id: int, req: OrderRequest) -> Order | None:
    existing = db.execute(
        select(Order).where(Order.user_id == user_id, Order.idempotency_key == req.idempotency_key)
    ).scalar_one_or_none()
    if existing is not None and existing.request_hash != req.digest():
        raise AppError(
            409, "IDEMPOTENCY_CONFLICT", "Idempotency key was already used for a different order"
        )
    return existing


def _check_tradable(asset: MarketAsset, channel: Channel | None, user: User) -> None:
    if asset.trading_status != "active" or channel is None or channel.listing_status != "listed":
        raise AppError(409, "TRADING_HALTED", asset.halt_reason or "Trading is halted for this asset")
    if user.role != "system":
        if user.status != "active":
            raise AppError(403, "ACCOUNT_RESTRICTED", "Trading is restricted for this account")
        if user.risk_level == "high":
            raise AppError(
                423,
                "ORDER_HELD_FOR_REVIEW",
                "New orders are on hold pending an operator review of recent activity. "
                "You can submit an appeal from your account page.",
            )


def _protect_price(db: Session, asset: MarketAsset, side: str, order_type: str, limit_price: int | None) -> int:
    """Limit price, or the worst acceptable price for a market order (server-defined)."""
    s = get_settings()
    ref = asset.reference_price
    if order_type == "limit":
        band = price_band(db, asset)
        lo, hi = max(s.min_price, bps_down(ref, band)), bps_up(ref, band)
        if not (lo <= limit_price <= hi):  # type: ignore[operator]
            raise AppError(422, "PRICE_OUT_OF_BAND", f"Limit price must be between {lo} and {hi}",
                           {"min": lo, "max": hi})
        return limit_price  # type: ignore[return-value]
    dev = s.market_order_max_deviation_bps
    return bps_up(ref, dev) if side == "buy" else max(1, bps_down(ref, dev))


def _collect_makers(db: Session, asset_id: int, side: str, protect: int, quantity: int, user_id: int):
    """Resting opposite orders we could match, in price-time priority (locked)."""
    opposite = "sell" if side == "buy" else "buy"
    q = select(Order).where(Order.asset_id == asset_id, Order.side == opposite, Order.status.in_(RESTING))
    if side == "buy":
        q = q.where(Order.limit_price <= protect).order_by(Order.limit_price.asc(), Order.id.asc())
    else:
        q = q.where(Order.limit_price >= protect).order_by(Order.limit_price.desc(), Order.id.asc())
    makers: list[Order] = []
    needed = quantity
    self_trade_blocked = False
    candidates = list(db.execute(q.limit(MAX_MATCH_DEPTH).with_for_update()).scalars())
    depth_exhausted = len(candidates) == MAX_MATCH_DEPTH
    for resting in candidates:
        if needed <= 0:
            break
        if resting.user_id == user_id:
            self_trade_blocked = True
            break
        makers.append(resting)
        needed -= resting.remaining_quantity
    return makers, self_trade_blocked, depth_exhausted


def place_order(db: Session, user: User, req: OrderRequest) -> PlaceResult:
    """Validate, reserve and match an order. Caller commits (see run_in_transaction).
    Outside trading hours the order is accepted as a reservation ('scheduled')."""
    _validate_shape(req)
    existing = _find_existing(db, user.id, req)
    if existing is not None:
        return PlaceResult(order=existing, replayed=True)

    asset = lock_asset(db, req.asset_id)
    # Re-check after acquiring the asset lock: a concurrent retry may have just committed.
    existing = _find_existing(db, user.id, req)
    if existing is not None:
        return PlaceResult(order=existing, replayed=True)
    _check_tradable(asset, db.get(Channel, asset.channel_id), user)

    # ---- price checks (server-defined; client never supplies execution prices or fees)
    protect = _protect_price(db, asset, req.side, req.order_type, req.limit_price)
    now = utcnow()
    # Fee rate is fixed now (surcharge windows may open/close while the order rests).
    fee_bps = fee_policy(asset, now).bps
    market_open = market_hours.is_open(now)

    if market_open:
        makers, self_trade_blocked, depth_exhausted = _collect_makers(
            db, asset.id, req.side, protect, req.quantity, user.id)
    else:
        makers, self_trade_blocked, depth_exhausted = [], False, False
    accounts = lock_accounts(db, {user.id} | {m.user_id for m in makers})
    account = accounts[user.id]

    # ---- reserve funds / shares for the incoming order
    reserve_cash = 0
    if req.side == "buy":
        reserve_cash = buy_reservation(req.quantity, protect, fee_bps)
        if account.cash_balance - account.reserved_cash < reserve_cash:
            raise AppError(
                409,
                "INSUFFICIENT_FUNDS",
                "Available cash is not enough for this order (including the maximum fee)",
                {"required": reserve_cash, "available": account.cash_balance - account.reserved_cash},
            )
    else:
        pos = _get_position(db, user.id, asset.id, create=False)
        available = (pos.quantity - pos.reserved_quantity) if pos else 0
        if available < req.quantity:
            raise AppError(
                409,
                "INSUFFICIENT_SHARES",
                "Available shares are not enough for this order",
                {"required": req.quantity, "available": available},
            )

    base = dict(user_id=user.id, asset_id=asset.id, side=req.side, order_type=req.order_type,
                quantity=req.quantity, remaining_quantity=req.quantity, limit_price=req.limit_price,
                fee_bps=fee_bps, protect_price=protect if req.order_type == "market" else None,
                idempotency_key=req.idempotency_key, request_hash=req.digest())

    if market_open and req.order_type == "market" and not makers:
        # No counterparty within price protection: record a rejection, never a fake fill.
        order = Order(**base, reserved_cash=0, status="rejected",
                      status_reason="SELF_TRADE_PREVENTED" if self_trade_blocked else "NO_LIQUIDITY")
        db.add(order)
        db.flush()
        return PlaceResult(order=order)

    order = Order(**base, reserved_cash=reserve_cash, status="open" if market_open else "scheduled")
    db.add(order)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        # A concurrent request with the same idempotency key won the race.
        raise AppError(409, "DUPLICATE_REQUEST", "This order request is already being processed")

    if req.side == "buy":
        account.reserved_cash += reserve_cash
    else:
        pos = _get_position(db, user.id, asset.id, create=False)
        assert pos is not None
        pos.reserved_quantity += req.quantity

    if not market_open:
        order.status_reason = "RESERVED_UNTIL_OPEN"
        db.flush()
        notify(db, user.id, "order_scheduled", f"{asset.symbol} 예약 주문 접수",
               f"장 시작 시 주문이 제출됩니다 · 주문 #{order.id}", f"/assets/{asset.id}")
        return PlaceResult(order=order)

    trades = _match_and_finalize(db, asset, order, makers, self_trade_blocked, depth_exhausted, accounts, now)
    return PlaceResult(order=order, trades=trades)


def _match_and_finalize(db, asset, order, makers, self_trade_blocked, depth_exhausted, accounts, now):
    trades: list[Trade] = []
    for maker in makers:
        if order.remaining_quantity == 0:
            break
        qty = min(order.remaining_quantity, maker.remaining_quantity)
        price = maker.limit_price
        assert price is not None
        buy_order, sell_order = (order, maker) if order.side == "buy" else (maker, order)
        trades.append(_execute(db, asset, buy_order, sell_order, qty, price, order.side, accounts, now))

    if order.remaining_quantity == 0:
        order.status = "filled"
    elif order.order_type == "market":
        order.status = "canceled"
        order.status_reason = "PARTIAL_FILL_REMAINDER_CANCELED" if trades else "NO_LIQUIDITY"
    elif self_trade_blocked:
        order.status = "canceled"
        order.status_reason = "SELF_TRADE_PREVENTED"
    elif depth_exhausted and len(makers) == MAX_MATCH_DEPTH:
        # Remainder could still cross the book; never leave a crossed book behind.
        order.status = "canceled"
        order.status_reason = "MATCH_DEPTH_LIMIT"
    else:
        order.status = "partially_filled" if order.remaining_quantity < order.quantity else "open"
        order.status_reason = None
    if order.status in ("filled", "canceled"):
        _release_remaining(db, order, accounts[order.user_id])
    order.updated_at = now
    db.flush()
    _notify_fills(db, asset, order, trades)
    return trades


def activate_scheduled(db: Session, order_id: int) -> list[Trade]:
    """Submit one reservation to the book (called at session open, oldest first)."""
    peek = db.get(Order, order_id)
    if peek is None:
        return []
    asset = lock_asset(db, peek.asset_id)
    order = db.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one()
    if order.status != "scheduled" or asset.trading_status != "active":
        return []  # halted assets keep their reservations until trading resumes
    now = utcnow()
    try:
        protect = _protect_price(db, asset, order.side, order.order_type, order.limit_price)
    except AppError:
        # The reference price moved and the limit is no longer inside today's price band.
        _release_remaining(db, order, lock_accounts(db, {order.user_id})[order.user_id])
        order.status, order.status_reason, order.updated_at = "canceled", "OUT_OF_BAND_AT_OPEN", now
        notify(db, order.user_id, "order_canceled", f"{asset.symbol} 예약 주문 취소",
               "지정가가 오늘의 가격 제한 범위를 벗어나 예약 주문이 취소되었습니다.", f"/assets/{asset.id}")
        return []
    if order.order_type == "market" and order.protect_price is not None:
        # Never exceed the price the reservation was sized for.
        protect = min(protect, order.protect_price) if order.side == "buy" else max(protect, order.protect_price)
    order.status = "open"
    makers, stp, depth = _collect_makers(db, asset.id, order.side, protect, order.remaining_quantity, order.user_id)
    accounts = lock_accounts(db, {order.user_id} | {m.user_id for m in makers})
    return _match_and_finalize(db, asset, order, makers, stp, depth, accounts, now)


def _notify_fills(db: Session, asset: MarketAsset, taker: Order, trades: list[Trade]) -> None:
    if not trades:
        return
    link = f"/assets/{asset.id}"
    side = {"buy": "매수", "sell": "매도"}
    filled = sum(t.quantity for t in trades)
    avg = sum(t.quantity * t.execution_price for t in trades) // filled
    notify(db, taker.user_id, "order_filled", f"{asset.symbol} {side[taker.side]} 체결",
           f"{filled}주 체결 (평균 {avg:,} 크레딧) · 주문 #{taker.id}", link)
    for t in trades:
        maker_order_id = t.sell_order_id if taker.side == "buy" else t.buy_order_id
        maker_user = t.seller_id if taker.side == "buy" else t.buyer_id
        maker_side = "sell" if taker.side == "buy" else "buy"
        notify(db, maker_user, "order_filled", f"{asset.symbol} {side[maker_side]} 체결",
               f"{t.quantity}주 @ {t.execution_price:,} 크레딧 · 주문 #{maker_order_id}", link)


def _execute(
    db: Session,
    asset: MarketAsset,
    buy_order: Order,
    sell_order: Order,
    qty: int,
    price: int,
    taker_side: str,
    accounts: dict[int, Account],
    now,
) -> Trade:
    notional = qty * price
    buyer_fee = fee_for(notional, buy_order.fee_bps)
    buyer_acct = accounts[buy_order.user_id]
    seller_acct = accounts[sell_order.user_id]

    # Buyer: consume reservation, then debit.
    consumed = notional + buyer_fee
    if consumed > buy_order.reserved_cash:  # guaranteed by buy_reservation(); defensive
        raise AppError(500, "RESERVATION_INVARIANT", "Reservation invariant violated")
    buy_order.reserved_cash -= consumed
    buyer_acct.reserved_cash -= consumed

    seller_pos = _get_position(db, sell_order.user_id, asset.id, create=False)
    assert seller_pos is not None
    # Surcharged orders that sell at a loss (vs. average cost) pay no sell fee.
    surcharged = sell_order.fee_bps > get_settings().fee_bps
    waived = surcharged and Decimal(notional) < seller_pos.average_cost * qty
    seller_fee = 0 if waived else fee_for(notional, sell_order.fee_bps)
    realized = Decimal(notional - seller_fee) - seller_pos.average_cost * qty

    trade = Trade(
        trade_uid=uuid.uuid4(),
        asset_id=asset.id,
        buy_order_id=buy_order.id,
        sell_order_id=sell_order.id,
        buyer_id=buy_order.user_id,
        seller_id=sell_order.user_id,
        quantity=qty,
        execution_price=price,
        buyer_fee=buyer_fee,
        seller_fee=seller_fee,
        taker_side=taker_side,
        seller_realized_pnl=realized,
        seller_fee_waived=waived,
        executed_at=now,
    )
    db.add(trade)
    db.flush()
    txid = trade.trade_uid

    post_entry(db, buyer_acct, entry_type="trade_buy", amount=-notional, transaction_id=txid,
               reference_type="trade", reference_id=trade.id)
    if buyer_fee:
        post_entry(db, buyer_acct, entry_type="trade_fee", amount=-buyer_fee, transaction_id=txid,
                   reference_type="trade", reference_id=trade.id)
    post_entry(db, seller_acct, entry_type="trade_sell", amount=notional, transaction_id=txid,
               reference_type="trade", reference_id=trade.id)
    if seller_fee:
        post_entry(db, seller_acct, entry_type="trade_fee", amount=-seller_fee, transaction_id=txid,
                   reference_type="trade", reference_id=trade.id)

    # Seller position
    seller_pos.reserved_quantity -= qty
    seller_pos.quantity -= qty
    seller_pos.realized_pnl += realized
    if seller_pos.quantity == 0:
        seller_pos.average_cost = Decimal(0)
    seller_pos.updated_at = now

    # Buyer position (average cost includes the buy fee)
    buyer_pos = _get_position(db, buy_order.user_id, asset.id, create=True)
    assert buyer_pos is not None
    total_cost = buyer_pos.average_cost * buyer_pos.quantity + notional + buyer_fee
    buyer_pos.quantity += qty
    buyer_pos.average_cost = (total_cost / buyer_pos.quantity).quantize(Decimal("0.000001"))
    buyer_pos.updated_at = now

    for o in (buy_order, sell_order):
        o.remaining_quantity -= qty
        o.updated_at = now
    for maker in (buy_order, sell_order):
        if maker.side != taker_side:
            if maker.remaining_quantity == 0:
                maker.status = "filled"
                _release_remaining(db, maker, accounts[maker.user_id])
            else:
                maker.status = "partially_filled"

    asset.current_price = price
    asset.last_trade_at = now
    asset.version += 1
    bars.record_trade_bar(db, asset.id, price, qty, now)
    return trade


def _release_remaining(db: Session, order: Order, account: Account) -> None:
    """Return whatever is still reserved for a finished (filled/canceled) order."""
    if order.side == "buy":
        if order.reserved_cash:
            account.reserved_cash -= order.reserved_cash
            order.reserved_cash = 0
    elif order.remaining_quantity:
        pos = _get_position(db, order.user_id, order.asset_id, create=False)
        assert pos is not None
        pos.reserved_quantity -= order.remaining_quantity


def cancel_order(db: Session, user: User, order_id: int, reason: str = "USER_CANCELED") -> Order:
    peek = db.get(Order, order_id)
    # Not-found and not-yours are indistinguishable to prevent order-ID enumeration (IDOR).
    if peek is None or peek.user_id != user.id:
        raise AppError(404, "ORDER_NOT_FOUND", "Order not found")
    lock_asset(db, peek.asset_id)
    order = db.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one()
    if order.status not in RESERVING:
        raise AppError(409, "ORDER_NOT_CANCELABLE", f"Order is already {order.status}")
    account = lock_accounts(db, {user.id})[user.id]
    _release_remaining(db, order, account)
    order.status = "canceled"
    order.status_reason = reason
    order.updated_at = utcnow()
    db.flush()
    return order


def cancel_all_for_asset(db: Session, asset_id: int, user_id: int | None, reason: str) -> int:
    """Cancel resting orders on an asset (optionally only one user's). Caller holds asset lock."""
    q = select(Order).where(Order.asset_id == asset_id, Order.status.in_(RESERVING))
    if user_id is not None:
        q = q.where(Order.user_id == user_id)
    orders = list(db.execute(q.order_by(Order.id).with_for_update()).scalars())
    if not orders:
        return 0
    accounts = lock_accounts(db, {o.user_id for o in orders})
    now = utcnow()
    for o in orders:
        _release_remaining(db, o, accounts[o.user_id])
        o.status = "canceled"
        o.status_reason = reason
        o.updated_at = now
    db.flush()
    return len(orders)
