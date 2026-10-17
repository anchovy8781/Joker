"""Shared serialisation helpers for API responses."""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Channel, MarketAsset, Order, Trade
from app.schemas import AssetOut, ChannelOut, FreshnessOut, OrderOut
from app.security import utcnow
from app.services.fees import fee_policy


def freshness(channel: Channel) -> FreshnessOut:
    s = get_settings()
    if channel.last_data_update_at is None:
        return FreshnessOut(status="never", last_update_at=None, age_hours=None,
                            stale_after_hours=s.stale_after_hours)
    age = (utcnow() - channel.last_data_update_at).total_seconds() / 3600
    return FreshnessOut(status="stale" if age > s.stale_after_hours else "fresh",
                        last_update_at=channel.last_data_update_at, age_hours=round(age, 2),
                        stale_after_hours=s.stale_after_hours)


def activity_24h(db: Session, asset_ids: list[int]) -> dict[int, tuple[int, int, int]]:
    """asset_id -> (volume, trade count, turnover) over the last 24h."""
    if not asset_ids:
        return {}
    since = utcnow() - timedelta(hours=24)
    rows = db.execute(
        select(Trade.asset_id, func.coalesce(func.sum(Trade.quantity), 0), func.count(Trade.id),
               func.coalesce(func.sum(Trade.quantity * Trade.execution_price), 0))
        .where(Trade.asset_id.in_(asset_ids), Trade.executed_at >= since)
        .group_by(Trade.asset_id)
    ).all()
    return {a: (int(v), int(n), int(t)) for a, v, n, t in rows}


def asset_out(asset: MarketAsset, channel: Channel, act: tuple[int, int, int] = (0, 0, 0)) -> AssetOut:
    policy = fee_policy(asset, utcnow())
    change = asset.current_price - asset.previous_price
    return AssetOut(
        id=asset.id,
        symbol=asset.symbol,
        channel=ChannelOut.model_validate(channel),
        reference_price=asset.reference_price,
        previous_price=asset.previous_price,
        current_price=asset.current_price,
        change=change,
        change_pct=round(change / asset.previous_price * 100, 4) if asset.previous_price else 0.0,
        shares_outstanding=asset.shares_outstanding,
        trading_status=asset.trading_status,
        halt_reason=asset.halt_reason,
        price_updated_at=asset.price_updated_at,
        last_trade_at=asset.last_trade_at,
        volume_24h=act[0],
        trades_24h=act[1],
        turnover_24h=act[2],
        market_cap=asset.current_price * asset.shares_outstanding,
        popularity_score=asset.popularity_score,
        popularity_updated_at=asset.popularity_updated_at,
        fee_bps=policy.bps,
        fee_surcharge_reason=policy.reason,
        fee_surcharge_until=policy.until,
        freshness=freshness(channel),
        is_demo=channel.data_source == "demo",
    )


def order_out(o: Order) -> OrderOut:
    return OrderOut(
        id=o.id, asset_id=o.asset_id, side=o.side, order_type=o.order_type, quantity=o.quantity,
        remaining_quantity=o.remaining_quantity, filled_quantity=o.quantity - o.remaining_quantity,
        limit_price=o.limit_price, reserved_cash=o.reserved_cash, fee_bps=o.fee_bps, status=o.status,
        status_reason=o.status_reason, idempotency_key=o.idempotency_key, created_at=o.created_at,
        updated_at=o.updated_at,
    )
