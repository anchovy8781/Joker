"""Turnover and trade count on bars, so 24-hour activity is summed from minute bars.

With the heartbeat trading every asset every few seconds, the trades table grows by tens
of thousands of rows per asset per day; summing it for every market list was getting
slow. Minute bars now carry turnover (price x quantity) and the number of trades, and
the last 25 hours are backfilled from trades.

Revision ID: 0012
Revises: 0011
"""
import os
import runpy

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_PREV = runpy.run_path(os.path.join(os.path.dirname(__file__), "0011_tape_fill_function.py"))["FUNCTION"]
_OLD = """    INSERT INTO market_bars (asset_id, interval, timestamp, open_price, high_price, low_price, close_price, volume)
    VALUES (p_asset, '1m', date_trunc('minute', now_ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC', price, price, price, price, qty),
           (p_asset, '1h', date_trunc('hour', now_ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC', price, price, price, price, qty),
           (p_asset, '1d', date_trunc('day', now_ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC', price, price, price, price, qty)
    ON CONFLICT (asset_id, interval, timestamp) DO UPDATE SET
      high_price = GREATEST(market_bars.high_price, EXCLUDED.high_price),
      low_price = LEAST(market_bars.low_price, EXCLUDED.low_price),
      close_price = EXCLUDED.close_price, volume = market_bars.volume + EXCLUDED.volume;"""
_NEW = """    INSERT INTO market_bars (asset_id, interval, timestamp, open_price, high_price, low_price, close_price, volume,
                             turnover, trade_count)
    VALUES (p_asset, '1m', date_trunc('minute', now_ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC', price, price, price, price, qty, notional, 1),
           (p_asset, '1h', date_trunc('hour', now_ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC', price, price, price, price, qty, notional, 1),
           (p_asset, '1d', date_trunc('day', now_ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC', price, price, price, price, qty, notional, 1)
    ON CONFLICT (asset_id, interval, timestamp) DO UPDATE SET
      high_price = GREATEST(market_bars.high_price, EXCLUDED.high_price),
      low_price = LEAST(market_bars.low_price, EXCLUDED.low_price),
      close_price = EXCLUDED.close_price, volume = market_bars.volume + EXCLUDED.volume,
      turnover = market_bars.turnover + EXCLUDED.turnover, trade_count = market_bars.trade_count + EXCLUDED.trade_count;"""
assert _OLD in _PREV
FUNCTION = _PREV.replace(_OLD, _NEW)


def upgrade() -> None:
    op.execute("ALTER TABLE market_bars ADD COLUMN IF NOT EXISTS turnover bigint NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE market_bars ADD COLUMN IF NOT EXISTS trade_count integer NOT NULL DEFAULT 0")
    op.execute("""
    UPDATE market_bars b SET turnover = x.t, trade_count = x.n
    FROM (SELECT asset_id, date_trunc('minute', executed_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS m,
                 sum(quantity * execution_price) AS t, count(*) AS n
          FROM trades WHERE executed_at > now() - interval '25 hours' GROUP BY 1, 2) x
    WHERE b.interval = '1m' AND b.asset_id = x.asset_id AND b.timestamp = x.m
    """)
    op.execute(FUNCTION)


def downgrade() -> None:
    op.execute(_PREV)
    op.execute("ALTER TABLE market_bars DROP COLUMN IF EXISTS trade_count")
    op.execute("ALTER TABLE market_bars DROP COLUMN IF EXISTS turnover")
