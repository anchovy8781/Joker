"""Shared serialisation helpers for API responses."""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Channel, MarketAsset, MarketBar, Order, Trade
from app.schemas import AssetOut, ChannelOut, FreshnessOut, OrderOut
from app.security import utcnow
from app.services import krx
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
    """asset_id -> (volume, trade count, turnover) over the last 24h, summed from minute bars
    (at most 1,440 rows per asset) instead of every trade."""
    if not asset_ids:
        return {}
    since = (utcnow() - timedelta(hours=24)).replace(second=0, microsecond=0)
    rows = db.execute(
        select(MarketBar.asset_id, func.coalesce(func.sum(MarketBar.volume), 0),
               func.coalesce(func.sum(MarketBar.trade_count), 0), func.coalesce(func.sum(MarketBar.turnover), 0))
        .where(MarketBar.asset_id.in_(asset_ids), MarketBar.interval == "1m", MarketBar.timestamp >= since)
        .group_by(MarketBar.asset_id)
    ).all()
    return {a: (int(v), int(n), int(t)) for a, v, n, t in rows}


def sparklines(db: Session, asset_ids: list[int]) -> dict[int, list[int]]:
    """asset_id -> recent closing prices for list thumbnails: hourly over 48h, or daily over
    two weeks when an asset traded too little for an hourly line."""
    if not asset_ids:
        return {}
    now = utcnow()
    out: dict[int, list[int]] = {}
    for interval, since in (("1h", now - timedelta(hours=48)), ("1d", now - timedelta(days=14))):
        rows = db.execute(
            select(MarketBar.asset_id, MarketBar.close_price)
            .where(MarketBar.asset_id.in_(asset_ids), MarketBar.interval == interval, MarketBar.timestamp >= since)
            .order_by(MarketBar.asset_id, MarketBar.timestamp)).all()
        series: dict[int, list[int]] = {}
        for aid, close in rows:
            series.setdefault(aid, []).append(int(close))
        for aid, pts in series.items():
            if aid not in out and len(pts) >= 3:
                out[aid] = pts
    return out


def asset_out(asset: MarketAsset, channel: Channel, act: tuple[int, int, int] = (0, 0, 0),
              spark: list[int] | None = None) -> AssetOut:
    policy = fee_policy(asset, utcnow())
    change = asset.current_price - asset.previous_price
    s = get_settings()  # same band rule as orders.price_band, from the 24h trade count we already have
    band = s.low_liquidity_band_bps if act[1] < s.low_liquidity_daily_trades else s.limit_band_bps
    lo, hi = krx.limits(asset.reference_price, band)
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
        sparkline=spark + [asset.current_price] if spark else [],
        popularity_score=asset.popularity_score,
        popularity_updated_at=asset.popularity_updated_at,
        fee_bps=policy.bps,
        fee_surcharge_reason=policy.reason,
        fee_surcharge_until=policy.until,
        freshness=freshness(channel),
        is_demo=channel.data_source == "demo",
        tick_size=krx.tick_size(max(1, asset.current_price)),
        lower_limit=lo,
        upper_limit=hi,
        vi_until=asset.vi_until,
        vi_kind=asset.vi_kind,
    )


def order_out(o: Order) -> OrderOut:
    return OrderOut(
        id=o.id, asset_id=o.asset_id, side=o.side, order_type=o.order_type, quantity=o.quantity,
        remaining_quantity=o.remaining_quantity, filled_quantity=o.quantity - o.remaining_quantity,
        limit_price=o.limit_price, reserved_cash=o.reserved_cash, fee_bps=o.fee_bps, status=o.status,
        status_reason=o.status_reason, idempotency_key=o.idempotency_key, created_at=o.created_at,
        updated_at=o.updated_at,
    )
