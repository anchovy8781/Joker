"""OHLCV bar maintenance from real executions and official reference prices."""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

_UPSERT = text(
    """
    INSERT INTO market_bars (asset_id, interval, timestamp, open_price, high_price, low_price,
                             close_price, volume)
    VALUES (:a, :i, :t, :p, :p, :p, :p, :v)
    ON CONFLICT (asset_id, interval, timestamp) DO UPDATE SET
        high_price = GREATEST(market_bars.high_price, EXCLUDED.high_price),
        low_price = LEAST(market_bars.low_price, EXCLUDED.low_price),
        close_price = EXCLUDED.close_price,
        volume = market_bars.volume + EXCLUDED.volume
    """
)


def bucket(ts: datetime, interval: str) -> datetime:
    if interval == "1m":
        return ts.replace(second=0, microsecond=0)
    if interval == "1h":
        return ts.replace(minute=0, second=0, microsecond=0)
    return ts.replace(hour=0, minute=0, second=0, microsecond=0)


def record_trade_bar(db: Session, asset_id: int, price: int, quantity: int, ts: datetime) -> None:
    for interval in ("1m", "1h", "1d"):
        db.execute(
            _UPSERT, {"a": asset_id, "i": interval, "t": bucket(ts, interval), "p": price, "v": quantity}
        )


def record_reference_bar(db: Session, asset_id: int, price: int, ts: datetime) -> None:
    """Make sure a daily bar exists for the official price even when nothing traded."""
    db.execute(_UPSERT, {"a": asset_id, "i": "1d", "t": bucket(ts, "1d"), "p": price, "v": 0})


def prune_minute_bars(db: Session, keep_days: int = 7) -> int:
    """Minute bars are only for intraday charts; hourly/daily bars keep the long history."""
    return db.execute(text("DELETE FROM market_bars WHERE interval = '1m' AND timestamp < now() - make_interval(days => :d)"),
                      {"d": keep_days}).rowcount
