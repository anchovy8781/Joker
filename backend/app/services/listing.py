"""Channel registration/listing, trading halts and the IPO market maker.

The market maker is a normal account with role='system'. At listing it receives the
asset's full share inventory (shares, not credits). It quotes real resting limit orders
around the reference price, sells only shares it holds and buys only with cash it earned
from those sales — so it never creates credits and never produces fills without a
genuine counterparty.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Account, Channel, ChannelMetric, MarketAsset, Order, Position, Trade, User
from app.security import utcnow
from app.services import bars, market_hours
from app.services.audit import write_audit
from app.services.fees import bps_down, bps_up, buy_reservation
from app.services.ledger import lock_account, post_entry
from app.services.notify import notify, notify_holders
from app.services.orders import OrderRequest, cancel_all_for_asset, lock_asset, place_order
from app.services.youtube import ChannelSnapshot, DemoProvider

MM_USERNAME = "market_maker"
AUTO_PREFIX = "AUTO:"
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
DEMO_ID_RE = re.compile(r"^DEMO[A-Za-z0-9_-]{2,40}$")


def get_market_maker(db: Session) -> User:
    mm = db.execute(select(User).where(User.normalized_username == MM_USERNAME)).scalar_one_or_none()
    if mm is None:
        mm = User(username=MM_USERNAME, normalized_username=MM_USERNAME, role="system",
                  password_hash=None, verification_status="verified")
        db.add(mm)
        db.flush()
        db.add(Account(user_id=mm.id, cash_balance=0, reserved_cash=0))
        db.flush()
    return mm


def register_channel(
    db: Session, *, youtube_channel_id: str, title: str, data_source: str,
    category: str | None, actor_id: int | None, request_id: str | None = None,
    requested_by: int | None = None, request_note: str | None = None,
) -> Channel:
    if data_source == "youtube_api" and not CHANNEL_ID_RE.match(youtube_channel_id):
        raise AppError(422, "INVALID_CHANNEL_ID", "YouTube channel IDs look like UC + 22 characters")
    if data_source == "demo" and not DEMO_ID_RE.match(youtube_channel_id):
        raise AppError(422, "INVALID_CHANNEL_ID", "Demo channel IDs must start with DEMO")
    if db.execute(select(Channel.id).where(Channel.youtube_channel_id == youtube_channel_id)).first():
        raise AppError(409, "CHANNEL_EXISTS", "This channel is already registered")
    ch = Channel(youtube_channel_id=youtube_channel_id, title=title[:200], data_source=data_source,
                 category=category, listing_status="pending", requested_by=requested_by,
                 request_note=request_note)
    db.add(ch)
    db.flush()
    write_audit(db, "channel.register", actor_id=actor_id, target_type="channel", target_id=ch.id,
                request_id=request_id, metadata={"youtube_channel_id": youtube_channel_id,
                                                 "data_source": data_source,
                                                 "requested_by": requested_by})
    return ch


def store_snapshot(db: Session, channel: Channel, snap: ChannelSnapshot, observed_at: datetime) -> None:
    from sqlalchemy import text

    values = {"view_count": snap.view_count, "video_count": snap.video_count}
    if snap.subscriber_count is not None:
        values["subscriber_count"] = snap.subscriber_count
    for metric_type, value in values.items():
        db.execute(
            text(
                "INSERT INTO channel_metrics (channel_id, metric_type, metric_value, observed_at, "
                "observed_date, source, source_timestamp, data_quality_status) "
                "VALUES (:c, :t, :v, :o, :d, :s, :st, :q) "
                "ON CONFLICT (channel_id, metric_type, source, observed_date) DO UPDATE SET "
                "metric_value = EXCLUDED.metric_value, observed_at = EXCLUDED.observed_at, "
                "source_timestamp = EXCLUDED.source_timestamp"
            ),
            {"c": channel.id, "t": metric_type, "v": value, "o": observed_at,
             "d": observed_at.date(), "s": channel.data_source, "st": snap.source_timestamp,
             "q": "ok"},
        )


def store_engagement(db: Session, channel: Channel, eng, observed_at: datetime) -> None:
    """Totals over recent uploads (likes may be hidden by the creator -> not stored)."""
    from sqlalchemy import text

    values = {"recent_view_count": eng.views}
    if eng.likes is not None:
        values["recent_like_count"] = eng.likes
    if eng.comments is not None:
        values["recent_comment_count"] = eng.comments
    for metric_type, value in values.items():
        db.execute(
            text(
                "INSERT INTO channel_metrics (channel_id, metric_type, metric_value, observed_at, "
                "observed_date, source, source_timestamp, data_quality_status) "
                "VALUES (:c, :t, :v, :o, :d, :s, NULL, 'ok') "
                "ON CONFLICT (channel_id, metric_type, source, observed_date) DO UPDATE SET "
                "metric_value = EXCLUDED.metric_value, observed_at = EXCLUDED.observed_at"
            ),
            {"c": channel.id, "t": metric_type, "v": value, "o": observed_at, "d": observed_at.date(),
             "s": channel.data_source},
        )


def backfill_demo_history(db: Session, channel: Channel, days: int = 35, now: datetime | None = None) -> None:
    """Demo channels get synthetic history so the engine can run immediately (labelled demo)."""
    assert channel.data_source == "demo"
    now = now or utcnow()
    demo = DemoProvider()
    for back in range(days, -1, -1):
        day = (now - timedelta(days=back)).date()
        snap = demo.snapshot_for(channel.youtube_channel_id, channel.title, day)
        at = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
        store_snapshot(db, channel, snap, at)
        store_engagement(db, channel, demo.engagement_for(channel.youtube_channel_id, day), at)
    channel.last_data_update_at = now
    channel.data_quality_status = "ok"


def approve_listing(
    db: Session, *, channel_id: int, symbol: str, initial_price: int, shares_outstanding: int,
    actor_id: int, request_id: str | None = None, celebrity_new_channel: bool = False,
) -> MarketAsset:
    """Every listing is an explicit administrator decision (recorded with the admin's id)."""
    s = get_settings()
    _require_admin(db, actor_id)
    db.flush()
    channel = db.execute(select(Channel).where(Channel.id == channel_id).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if channel is None:
        raise AppError(404, "CHANNEL_NOT_FOUND", "Channel not found")
    if channel.listing_status != "pending":
        raise AppError(409, "INVALID_LISTING_STATE", f"Channel is {channel.listing_status}")
    symbol = symbol.upper()
    if not SYMBOL_RE.match(symbol):
        raise AppError(422, "INVALID_SYMBOL", "Symbol must be 2-12 uppercase letters/digits")
    if db.execute(select(MarketAsset.id).where(MarketAsset.symbol == symbol)).first():
        raise AppError(409, "SYMBOL_TAKEN", "Symbol already in use")
    if not (s.min_price <= initial_price <= 10_000_000):
        raise AppError(422, "INVALID_PRICE", f"initial_price must be >= {s.min_price}")
    if not (1_000 <= shares_outstanding <= 1_000_000_000):
        raise AppError(422, "INVALID_SHARES", "shares_outstanding must be 1,000..1,000,000,000")
    if channel.data_source == "demo":
        backfill_demo_history(db, channel)
    now = utcnow()
    asset = MarketAsset(channel_id=channel.id, symbol=symbol, reference_price=initial_price,
                        previous_price=initial_price, current_price=initial_price,
                        shares_outstanding=shares_outstanding, trading_status="active",
                        price_updated_at=now)
    db.add(asset)
    channel.listing_status = "listed"
    channel.listed_at = now
    db.flush()
    if s.auto_halt_enabled and channel.data_source == "youtube_api" and channel.last_data_update_at is None:
        # Never trade on data we have not verified yet.
        asset.trading_status = "halted"
        asset.halt_reason = f"{AUTO_PREFIX}AWAITING_DATA — waiting for first YouTube API collection"
    mm = get_market_maker(db)
    db.add(Position(user_id=mm.id, asset_id=asset.id, quantity=shares_outstanding, reserved_quantity=0,
                    average_cost=Decimal(0), realized_pnl=Decimal(0)))
    bars.record_reference_bar(db, asset.id, initial_price, now)
    db.flush()
    write_audit(db, "asset.list", actor_id=actor_id, target_type="asset", target_id=asset.id,
                request_id=request_id, metadata={"symbol": symbol, "initial_price": initial_price,
                                                 "ipo_shares_to_market_maker": shares_outstanding,
                                                 "channel_id": channel.id,
                                                 "celebrity_new_channel": celebrity_new_channel})
    if celebrity_new_channel:
        asset.celebrity_until = now + timedelta(days=s.celebrity_surcharge_days)
    if channel.requested_by:
        notify(db, channel.requested_by, "listing_approved", f"상장 승인: {symbol}",
               f"신청하신 채널 '{channel.title}'이(가) {symbol}(으)로 상장되었습니다.", f"/assets/{asset.id}")
    if asset.trading_status == "active":
        requote_market_maker(db, asset.id)
    return asset


def _require_admin(db: Session, actor_id: int | None) -> None:
    actor = db.get(User, actor_id) if actor_id is not None else None
    if actor is None or actor.role != "admin":
        raise AppError(403, "FORBIDDEN", "Listing decisions require an administrator")


def reject_listing(db: Session, channel_id: int, reason: str, actor_id: int, request_id: str | None) -> Channel:
    _require_admin(db, actor_id)
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise AppError(404, "CHANNEL_NOT_FOUND", "Channel not found")
    if channel.listing_status != "pending":
        raise AppError(409, "INVALID_LISTING_STATE", f"Channel is {channel.listing_status}")
    channel.listing_status = "rejected"
    from app.services.listing_requests import refund_fee  # (circular at import time)
    refunded = refund_fee(db, channel, actor_id)
    if channel.requested_by:
        extra = f" 신청 비용 {refunded:,} 크레딧은 환불되었습니다." if refunded else ""
        notify(db, channel.requested_by, "listing_rejected", "상장 신청 거절",
               f"'{channel.title}' 상장 신청이 거절되었습니다. 사유: {reason}.{extra}", "/listings")
    write_audit(db, "channel.reject", actor_id=actor_id, target_type="channel", target_id=channel_id,
                request_id=request_id, metadata={"reason": reason, "fee_refunded": refunded})
    return channel


def halt(db: Session, asset_id: int, reason: str, actor_id: int | None, request_id: str | None = None) -> MarketAsset:
    asset = lock_asset(db, asset_id)
    before = asset.trading_status
    asset.trading_status = "halted"
    asset.halt_reason = reason[:200]
    asset.version += 1
    write_audit(db, "asset.halt", actor_id=actor_id, target_type="asset", target_id=asset_id,
                request_id=request_id, metadata={"reason": reason, "before": before})
    notify_holders(db, asset_id, "asset_halted", f"{asset.symbol} 거래 정지",
                   f"보유 종목 {asset.symbol}의 거래가 정지되었습니다. 사유: {reason}", f"/assets/{asset_id}")
    return asset


def delist(db: Session, asset_id: int, reason: str, actor_id: int, request_id: str | None = None,
           settlement_price: int | None = None) -> dict:
    """상장폐지: trading stops for good, every resting order and auto order on the stock is
    cancelled (reservations released), and every holder is paid out in credits at the
    settlement price (default: the last traded price): the market maker buys the shares back
    in fee-free settlement trades, funded by the system. Ledger, trades and the audit log
    record everything, so reconciliation stays exact."""
    from app.models import AutoOrder

    _require_admin(db, actor_id)
    asset = lock_asset(db, asset_id)
    channel = db.get(Channel, asset.channel_id)
    if channel.listing_status != "listed":
        raise AppError(409, "NOT_LISTED", "상장된 종목만 상장폐지할 수 있어요")
    price = settlement_price or asset.current_price or asset.reference_price
    if price <= 0:
        raise AppError(422, "INVALID_PRICE", "정산 가격이 올바르지 않아요")
    canceled = cancel_all_for_asset(db, asset_id, None, "DELISTED")
    autos = db.execute(select(AutoOrder).where(AutoOrder.asset_id == asset_id, AutoOrder.status == "active")).scalars().all()
    for a in autos:
        a.status = "canceled"
    mm = get_market_maker(db)
    holders = db.execute(select(Position).where(Position.asset_id == asset_id, Position.user_id != mm.id,
                                                Position.quantity > 0).with_for_update()).scalars().all()
    mm_pos = db.get(Position, (mm.id, asset_id))
    total = sum(p.quantity for p in holders) * price
    if total:
        # The market maker buys every share back at the settlement price, funded by the system,
        # so each payout is an ordinary fee-free trade in the books.
        post_entry(db, lock_account(db, mm.id), entry_type="system_funding", amount=total, transaction_id=uuid.uuid4(),
                   reference_type="delisting", reference_id=asset_id, actor_id=actor_id,
                   memo=f"{asset.symbol} 상장폐지 정산 자금")
    paid_total, paid_users = 0, 0
    for pos in holders:
        qty, payout = pos.quantity, pos.quantity * price
        orders = {}
        for side, uid in (("sell", pos.user_id), ("buy", mm.id)):
            req = OrderRequest(asset_id, side, "limit", qty, price, f"delist:{asset_id}:{pos.user_id}:{side}")
            o = Order(user_id=uid, asset_id=asset_id, side=side, order_type="limit", quantity=qty, remaining_quantity=0,
                      limit_price=price, reserved_cash=0, fee_bps=0, status="filled",
                      status_reason="DELISTING_SETTLEMENT", idempotency_key=req.idempotency_key,
                      request_hash=req.digest())
            db.add(o)
            orders[side] = o
        db.flush()
        realized = Decimal(payout) - pos.average_cost * qty
        trade = Trade(trade_uid=uuid.uuid4(), asset_id=asset_id, buy_order_id=orders["buy"].id,
                      sell_order_id=orders["sell"].id, buyer_id=mm.id, seller_id=pos.user_id, quantity=qty,
                      execution_price=price, buyer_fee=0, seller_fee=0, taker_side="sell",
                      seller_realized_pnl=realized, seller_fee_waived=False, executed_at=utcnow())
        db.add(trade)
        db.flush()
        post_entry(db, lock_account(db, mm.id), entry_type="trade_buy", amount=-payout, transaction_id=trade.trade_uid,
                   reference_type="trade", reference_id=trade.id)
        post_entry(db, lock_account(db, pos.user_id), entry_type="trade_sell", amount=payout,
                   transaction_id=trade.trade_uid, reference_type="trade", reference_id=trade.id)
        pos.realized_pnl += realized
        pos.quantity, pos.reserved_quantity, pos.average_cost = 0, 0, Decimal(0)
        mm_pos.quantity += qty
        paid_total += payout
        paid_users += 1
        notify(db, pos.user_id, "asset_delisted", f"{asset.symbol} 상장폐지",
               f"보유하신 {qty}주가 주당 {price:,} 크레딧으로 정산되어 {payout:,} 크레딧이 입금되었어요. 사유: {reason}",
               "/portfolio")
    mm_pos.reserved_quantity = 0
    asset.trading_status = "halted"
    asset.halt_reason = f"DELISTED — {reason}"[:200]
    asset.version += 1
    channel.listing_status = "delisted"
    write_audit(db, "asset.delist", actor_id=actor_id, target_type="asset", target_id=asset_id, request_id=request_id,
                metadata={"reason": reason, "settlement_price": price, "holders_paid": paid_users,
                          "credits_paid": paid_total, "orders_canceled": canceled, "auto_orders_canceled": len(autos)})
    db.flush()
    return {"asset_id": asset_id, "symbol": asset.symbol, "settlement_price": price, "holders_paid": paid_users,
            "credits_paid": paid_total, "orders_canceled": canceled}


def resume(db: Session, asset_id: int, reason: str, actor_id: int | None, request_id: str | None = None) -> MarketAsset:
    asset = lock_asset(db, asset_id)
    channel = db.get(Channel, asset.channel_id)
    from app.services.pricing import is_stale

    if get_settings().auto_halt_enabled and (channel.data_quality_status == "unavailable"
                                             or is_stale(channel, utcnow())):
        raise AppError(409, "DATA_NOT_FRESH", "Cannot resume: channel data is stale or unavailable")
    asset.trading_status = "active"
    asset.halt_reason = None
    asset.version += 1
    write_audit(db, "asset.resume", actor_id=actor_id, target_type="asset", target_id=asset_id,
                request_id=request_id, metadata={"reason": reason})
    requote_market_maker(db, asset_id)
    return asset


def auto_halt(db: Session, asset: MarketAsset, code: str, detail: str) -> None:
    """System halt for data problems. Never overrides a manual (operator) halt reason.
    Off by default (auto_halt_enabled): trading stays open and the data badge shows staleness."""
    if not get_settings().auto_halt_enabled:
        return
    if asset.trading_status == "halted" and not (asset.halt_reason or "").startswith(AUTO_PREFIX):
        return
    if asset.trading_status == "halted" and (asset.halt_reason or "").startswith(f"{AUTO_PREFIX}{code}"):
        return
    asset.trading_status = "halted"
    asset.halt_reason = f"{AUTO_PREFIX}{code} — {detail}"[:200]
    asset.version += 1
    write_audit(db, "asset.auto_halt", target_type="asset", target_id=asset.id,
                metadata={"code": code, "detail": detail})
    notify_holders(db, asset.id, "asset_halted", f"{asset.symbol} 자동 거래 정지",
                   f"채널 데이터 문제로 거래가 정지되었습니다: {detail}", f"/assets/{asset.id}")


def auto_resume_if_safe(db: Session, asset: MarketAsset) -> bool:
    if asset.trading_status != "halted" or not (asset.halt_reason or "").startswith(AUTO_PREFIX):
        return False
    asset.trading_status = "active"
    asset.halt_reason = None
    asset.version += 1
    write_audit(db, "asset.auto_resume", target_type="asset", target_id=asset.id,
                metadata={"reason": "channel data is fresh again"})
    requote_market_maker(db, asset.id)
    return True


def resume_auto_halts(db: Session) -> int:
    """With automatic halts switched off, lift any data halt left from before."""
    if get_settings().auto_halt_enabled:
        return 0
    n = 0
    for asset in db.execute(select(MarketAsset).where(MarketAsset.trading_status == "halted",
                                                       MarketAsset.halt_reason.like(f"{AUTO_PREFIX}%"))).scalars():
        n += auto_resume_if_safe(db, asset)
    return n


def fund_market_maker(db: Session, mm: User | None = None) -> int:
    """Keep the market maker's bid budget topped up from system funds so that selling is
    always possible. Returns the amount added (0 when already funded)."""
    s = get_settings()
    mm = mm or get_market_maker(db)
    n_assets = max(1, db.query(MarketAsset).count())
    target = s.mm_cash_per_asset * n_assets
    account = lock_account(db, mm.id)
    if account.cash_balance >= target // 2:
        return 0
    amount = target - account.cash_balance
    post_entry(db, account, entry_type="system_funding", amount=amount, transaction_id=uuid.uuid4(),
               reference_type="mm_liquidity", reference_id=mm.id, memo="Market-maker bid liquidity")
    return amount


def _bulk_cancel_mm(db: Session, asset_id: int, mm_id: int, reason: str) -> int:
    """Cancel the market maker's resting orders on one asset in a few statements (instead of
    one round trip per order) and release what they reserved. Caller holds the asset lock."""
    db.flush()
    row = db.execute(text(
        "WITH c AS (SELECT id, side, reserved_cash, remaining_quantity FROM orders "
        "           WHERE asset_id = :a AND user_id = :u AND status IN ('open', 'partially_filled', 'scheduled') "
        "           FOR UPDATE), "
        "u AS (UPDATE orders o SET status = 'canceled', status_reason = :r, updated_at = now(), "
        "      reserved_cash = CASE WHEN o.side = 'buy' THEN 0 ELSE o.reserved_cash END "
        "      FROM c WHERE o.id = c.id) "
        "SELECT count(*), coalesce(sum(reserved_cash) FILTER (WHERE side = 'buy'), 0), "
        "       coalesce(sum(remaining_quantity) FILTER (WHERE side = 'sell'), 0) FROM c"),
        {"a": asset_id, "u": mm_id, "r": reason}).one()
    n, cash, shares = int(row[0]), int(row[1]), int(row[2])
    if cash:
        db.execute(text("UPDATE accounts SET reserved_cash = reserved_cash - :c WHERE user_id = :u"),
                   {"c": cash, "u": mm_id})
    if shares:
        db.execute(text("UPDATE positions SET reserved_quantity = reserved_quantity - :q "
                        "WHERE user_id = :u AND asset_id = :a"), {"q": shares, "u": mm_id, "a": asset_id})
    if n:
        # Rows changed behind the ORM's back: drop cached copies so later reads are fresh.
        for obj in list(db.identity_map.values()):
            if isinstance(obj, (Order, Account, Position)):
                db.expire(obj)
    return n


def _resting(mm_id: int, req: OrderRequest, fee_bps: int, reserved_cash: int) -> Order:
    return Order(user_id=mm_id, asset_id=req.asset_id, side=req.side, order_type="limit", quantity=req.quantity,
                 remaining_quantity=req.quantity, limit_price=req.limit_price, fee_bps=fee_bps, protect_price=None,
                 idempotency_key=req.idempotency_key, request_hash=req.digest(), reserved_cash=reserved_cash,
                 status="open")


def requote_market_maker(db: Session, asset_id: int) -> dict:
    """Replace the market maker's quotes on one asset with a ladder of small orders around a
    centre that follows the last traded price (mm_follow_bps, 90%) and the intraday fair value, so
    real volume walks the price while fundamentals keep pulling it back.

    Every database statement is a network round trip, so the ladder is replaced in bulk:
    one statement cancels the old quotes, one inserts the new ones. Only a level that would
    cross someone else's resting order goes through the matching engine."""
    from app.services import intraday  # live data → quote centre (see intraday.py)
    from app.services.fees import fee_policy
    from app.services.orders import price_band

    s = get_settings()
    mm = get_market_maker(db)
    asset = lock_asset(db, asset_id)
    canceled = _bulk_cancel_mm(db, asset_id, mm.id, "MM_REQUOTE")
    placed = 0
    now = utcnow()
    if asset.trading_status != "active" or not market_hours.is_open(now):
        return {"canceled": canceled, "placed": 0}
    intraday.update(db, asset, mm.id)
    fair = intraday.fair_price(asset)
    last = asset.current_price or asset.reference_price
    centre = (last * s.mm_follow_bps + fair * (10_000 - s.mm_follow_bps)) // 10_000
    band = price_band(db, asset)
    lo, hi = max(s.min_price, bps_down(asset.reference_price, band)), bps_up(asset.reference_price, band)
    centre = min(max(centre, lo), hi)
    fund_market_maker(db, mm)
    account = lock_account(db, mm.id)
    pos = db.get(Position, (mm.id, asset_id))
    inventory = (pos.quantity - pos.reserved_quantity) if pos else 0
    n_assets = max(1, db.query(MarketAsset).count())
    cash_budget = (account.cash_balance - account.reserved_cash) // n_assets
    best_bid, best_ask = db.execute(text(
        "SELECT max(limit_price) FILTER (WHERE side = 'buy'), min(limit_price) FILTER (WHERE side = 'sell') "
        "FROM orders WHERE asset_id = :a AND user_id <> :u AND status IN ('open', 'partially_filled')"),
        {"a": asset_id, "u": mm.id}).one()
    fee_bps = fee_policy(asset, now).bps
    rows: list[Order] = []
    crossing: list[OrderRequest] = []
    for k in range(s.mm_levels):
        offset = s.mm_half_spread_bps + k * s.mm_step_bps
        size_value = s.mm_level_value * (2 + k) // 2  # deeper levels hold a little more
        ask = bps_up(centre, offset)
        if inventory > 0 and ask <= hi:
            qty = min(inventory, max(1, size_value // ask), s.max_order_quantity)
            inventory -= qty
            req = OrderRequest(asset_id, "sell", "limit", qty, ask, f"mm:{uuid.uuid4().hex}")
            if best_bid is not None and ask <= best_bid:
                crossing.append(req)
            else:
                rows.append(_resting(mm.id, req, fee_bps, 0))
        bid = bps_down(centre, offset)
        if bid >= lo:
            qty = min(max(1, size_value // bid), s.max_order_quantity)
            cost = buy_reservation(qty, bid, fee_bps)
            if cost <= cash_budget:
                cash_budget -= cost
                req = OrderRequest(asset_id, "buy", "limit", qty, bid, f"mm:{uuid.uuid4().hex}")
                if best_ask is not None and bid >= best_ask:
                    crossing.append(req)
                else:
                    rows.append(_resting(mm.id, req, fee_bps, cost))
    if rows:
        db.add_all(rows)
        account.reserved_cash += sum(o.reserved_cash for o in rows)
        sells = sum(o.quantity for o in rows if o.side == "sell")
        if sells:
            pos.reserved_quantity += sells
        db.flush()
        placed += len(rows)
    for req in crossing:  # would trade with a resting order: let the matching engine fill it
        r = place_order(db, mm, req)
        if r.order.status in ("open", "partially_filled", "filled"):
            placed += 1
    return {"canceled": canceled, "placed": placed, "centre": centre}
