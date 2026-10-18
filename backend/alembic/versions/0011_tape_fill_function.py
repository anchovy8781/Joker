"""ytx_tape_fill: the price heartbeat's market order against the market maker, in one call.

The app server and the database are in different regions, so every statement costs a
network round trip; a fill through the ORM matching engine takes ~25 of them. This function
does the same bookkeeping as orders._execute for the common heartbeat case (a bot's market
order that only meets the market maker's ladder) inside the database: one round trip.
Returns the last fill price; 0 when the bot lacks the cash or shares; NULL when someone
else's order is at the top of the book, so the caller uses the full matching engine. Same ledger rules: integer credits,
fees rounded up, balance never below reserved cash, ledger rows append-only.

Revision ID: 0011
Revises: 0010
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

FUNCTION = r"""
CREATE OR REPLACE FUNCTION ytx_tape_fill(p_asset bigint, p_bot bigint, p_side text, p_qty bigint,
                                         p_fee_bps int, p_base_fee_bps int, p_key text, p_max_levels int)
RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE
  a market_assets%ROWTYPE;
  p_mm bigint;
  mk orders%ROWTYPE;
  now_ts timestamptz := now();
  remaining bigint := p_qty;
  filled bigint := 0;
  taker_id bigint;
  qty bigint; price bigint; notional bigint; buyer_fee bigint; seller_fee bigint;
  buyer bigint; seller bigint; buyer_bps int; seller_bps int; waived boolean; realized numeric;
  sp positions%ROWTYPE; bp positions%ROWTYPE;
  acct accounts%ROWTYPE;
  trade_id bigint; tx uuid; bal bigint; last_price bigint := NULL; levels int := 0; broke boolean := false;
BEGIN
  IF p_side NOT IN ('buy', 'sell') OR p_qty <= 0 THEN RETURN NULL; END IF;
  SELECT id INTO p_mm FROM users WHERE normalized_username = 'market_maker';
  IF p_mm IS NULL THEN RETURN NULL; END IF;
  SELECT * INTO a FROM market_assets WHERE id = p_asset FOR UPDATE;
  IF NOT FOUND OR a.trading_status <> 'active' THEN RETURN NULL; END IF;

  WHILE remaining > 0 AND levels < p_max_levels LOOP
    -- best resting order on the other side (price, then time priority)
    IF p_side = 'buy' THEN
      SELECT * INTO mk FROM orders WHERE asset_id = p_asset AND side = 'sell'
        AND status IN ('open', 'partially_filled') ORDER BY limit_price ASC, id ASC LIMIT 1 FOR UPDATE;
    ELSE
      SELECT * INTO mk FROM orders WHERE asset_id = p_asset AND side = 'buy'
        AND status IN ('open', 'partially_filled') ORDER BY limit_price DESC, id ASC LIMIT 1 FOR UPDATE;
    END IF;
    EXIT WHEN NOT FOUND OR mk.user_id <> p_mm;   -- someone else's order: leave it to the full engine

    qty := least(remaining, mk.remaining_quantity);
    price := mk.limit_price;
    notional := qty * price;
    IF p_side = 'buy' THEN
      buyer := p_bot; seller := p_mm; buyer_bps := p_fee_bps; seller_bps := mk.fee_bps;
      SELECT * INTO acct FROM accounts WHERE user_id = p_bot FOR UPDATE;
      IF acct.cash_balance - acct.reserved_cash < notional + (notional * buyer_bps + 9999) / 10000 THEN
        broke := true; EXIT;
      END IF;
    ELSE
      buyer := p_mm; seller := p_bot; buyer_bps := mk.fee_bps; seller_bps := p_fee_bps;
      SELECT * INTO sp FROM positions WHERE user_id = p_bot AND asset_id = p_asset FOR UPDATE;
      IF NOT FOUND OR sp.quantity - sp.reserved_quantity < qty THEN
        broke := true; EXIT;
      END IF;
    END IF;

    IF taker_id IS NULL THEN
      INSERT INTO orders (user_id, asset_id, side, order_type, quantity, remaining_quantity, limit_price,
                          reserved_cash, fee_bps, protect_price, status, idempotency_key, request_hash)
      VALUES (p_bot, p_asset, p_side, 'market', p_qty, p_qty, NULL, 0, p_fee_bps, price, 'open', p_key,
              encode(sha256(convert_to(p_key, 'UTF8')), 'hex'))
      RETURNING id INTO taker_id;
    END IF;

    buyer_fee := (notional * buyer_bps + 9999) / 10000;
    SELECT * INTO sp FROM positions WHERE user_id = seller AND asset_id = p_asset FOR UPDATE;
    waived := seller_bps > p_base_fee_bps AND notional < sp.average_cost * qty;
    seller_fee := CASE WHEN waived THEN 0 ELSE (notional * seller_bps + 9999) / 10000 END;
    realized := (notional - seller_fee) - sp.average_cost * qty;
    tx := gen_random_uuid();

    INSERT INTO trades (trade_uid, asset_id, buy_order_id, sell_order_id, buyer_id, seller_id, quantity,
                        execution_price, buyer_fee, seller_fee, taker_side, seller_realized_pnl, seller_fee_waived,
                        executed_at)
    VALUES (tx, p_asset, CASE WHEN p_side = 'buy' THEN taker_id ELSE mk.id END,
            CASE WHEN p_side = 'buy' THEN mk.id ELSE taker_id END, buyer, seller, qty, price, buyer_fee,
            seller_fee, p_side, realized, waived, now_ts)
    RETURNING id INTO trade_id;

    -- buyer: a market-maker bid gives back the reservation this fill consumes
    IF buyer = p_mm THEN
      UPDATE accounts SET reserved_cash = reserved_cash - (notional + buyer_fee) WHERE user_id = p_mm;
      UPDATE orders SET reserved_cash = reserved_cash - (notional + buyer_fee) WHERE id = mk.id;
    END IF;
    UPDATE accounts SET cash_balance = cash_balance - notional, version = version + 1, updated_at = now_ts
      WHERE user_id = buyer RETURNING cash_balance INTO bal;
    INSERT INTO ledger_entries (transaction_id, user_id, entry_type, amount, balance_after, reference_type, reference_id)
      VALUES (tx, buyer, 'trade_buy', -notional, bal, 'trade', trade_id::text);
    IF buyer_fee > 0 THEN
      UPDATE accounts SET cash_balance = cash_balance - buyer_fee, version = version + 1 WHERE user_id = buyer
        RETURNING cash_balance INTO bal;
      INSERT INTO ledger_entries (transaction_id, user_id, entry_type, amount, balance_after, reference_type, reference_id)
        VALUES (tx, buyer, 'trade_fee', -buyer_fee, bal, 'trade', trade_id::text);
    END IF;
    UPDATE accounts SET cash_balance = cash_balance + notional, version = version + 1, updated_at = now_ts
      WHERE user_id = seller RETURNING cash_balance INTO bal;
    INSERT INTO ledger_entries (transaction_id, user_id, entry_type, amount, balance_after, reference_type, reference_id)
      VALUES (tx, seller, 'trade_sell', notional, bal, 'trade', trade_id::text);
    IF seller_fee > 0 THEN
      UPDATE accounts SET cash_balance = cash_balance - seller_fee, version = version + 1 WHERE user_id = seller
        RETURNING cash_balance INTO bal;
      INSERT INTO ledger_entries (transaction_id, user_id, entry_type, amount, balance_after, reference_type, reference_id)
        VALUES (tx, seller, 'trade_fee', -seller_fee, bal, 'trade', trade_id::text);
    END IF;
    IF EXISTS (SELECT 1 FROM accounts WHERE user_id IN (buyer, seller) AND (cash_balance < 0 OR cash_balance < reserved_cash)) THEN
      RAISE EXCEPTION 'ytx_tape_fill: balance below reserved cash';
    END IF;

    -- seller position (the market maker's shares were reserved by its ask)
    UPDATE positions SET quantity = quantity - qty,
           reserved_quantity = reserved_quantity - CASE WHEN seller = p_mm THEN qty ELSE 0 END,
           realized_pnl = realized_pnl + realized,
           average_cost = CASE WHEN quantity - qty = 0 THEN 0 ELSE average_cost END, updated_at = now_ts
      WHERE user_id = seller AND asset_id = p_asset;
    -- buyer position (average cost includes the buy fee)
    INSERT INTO positions (user_id, asset_id, quantity, reserved_quantity, average_cost, realized_pnl)
      VALUES (buyer, p_asset, 0, 0, 0, 0) ON CONFLICT (user_id, asset_id) DO NOTHING;
    SELECT * INTO bp FROM positions WHERE user_id = buyer AND asset_id = p_asset FOR UPDATE;
    UPDATE positions SET quantity = quantity + qty,
           average_cost = round((bp.average_cost * bp.quantity + notional + buyer_fee) / (bp.quantity + qty), 6),
           updated_at = now_ts
      WHERE user_id = buyer AND asset_id = p_asset;

    -- the market maker's order
    UPDATE orders SET remaining_quantity = remaining_quantity - qty, updated_at = now_ts,
           status = CASE WHEN remaining_quantity - qty = 0 THEN 'filled' ELSE 'partially_filled' END
      WHERE id = mk.id;
    IF mk.remaining_quantity - qty = 0 AND mk.side = 'buy' THEN   -- release what is left of a filled bid
      UPDATE accounts ac SET reserved_cash = ac.reserved_cash - o.reserved_cash FROM orders o
        WHERE o.id = mk.id AND ac.user_id = p_mm;
      UPDATE orders SET reserved_cash = 0 WHERE id = mk.id;
    END IF;

    -- bars and the last price
    INSERT INTO market_bars (asset_id, interval, timestamp, open_price, high_price, low_price, close_price, volume)
    VALUES (p_asset, '1m', date_trunc('minute', now_ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC', price, price, price, price, qty),
           (p_asset, '1h', date_trunc('hour', now_ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC', price, price, price, price, qty),
           (p_asset, '1d', date_trunc('day', now_ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC', price, price, price, price, qty)
    ON CONFLICT (asset_id, interval, timestamp) DO UPDATE SET
      high_price = GREATEST(market_bars.high_price, EXCLUDED.high_price),
      low_price = LEAST(market_bars.low_price, EXCLUDED.low_price),
      close_price = EXCLUDED.close_price, volume = market_bars.volume + EXCLUDED.volume;

    remaining := remaining - qty;
    filled := filled + qty;
    last_price := price;
    levels := levels + 1;
  END LOOP;

  IF taker_id IS NULL THEN
    RETURN CASE WHEN broke THEN 0 ELSE NULL END;   -- 0: no money/shares; NULL: use the full engine
  END IF;
  UPDATE orders SET remaining_quantity = remaining, updated_at = now_ts,
         status = CASE WHEN remaining = 0 THEN 'filled' ELSE 'canceled' END,
         status_reason = CASE WHEN remaining = 0 THEN NULL ELSE 'PARTIAL_FILL_REMAINDER_CANCELED' END
    WHERE id = taker_id;
  UPDATE market_assets SET current_price = last_price, last_trade_at = now_ts, version = version + 1 WHERE id = p_asset;
  RETURN last_price;
END
$fn$;
REVOKE ALL ON FUNCTION ytx_tape_fill(bigint, bigint, text, bigint, int, int, text, int) FROM PUBLIC;
"""


def upgrade() -> None:
    op.execute(FUNCTION)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS ytx_tape_fill(bigint, bigint, text, bigint, int, int, text, int)")
