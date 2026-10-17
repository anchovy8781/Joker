"""Minute/week/month candles and the volume profile (매물대)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.models import MarketAsset
from app.services import bars


def test_trades_write_minute_bars_and_rollups(make_user, asset, db):
    u = make_user()
    for _ in range(3):
        assert u.order(asset, "buy", 2).status_code == 201
    m1 = u.get(f"/api/assets/{asset}/history?interval=1m").json()
    assert m1 and sum(b["volume"] for b in m1) == 6
    m5 = u.get(f"/api/assets/{asset}/history?interval=5m").json()
    assert sum(b["volume"] for b in m5) == 6


def test_month_and_week_candles_roll_up_daily_bars(db, asset, make_user):
    db.execute(text("DELETE FROM market_bars WHERE asset_id = :a"), {"a": asset})
    db.commit()
    days = [(datetime(2026, 8, 30, tzinfo=UTC), 100, 5), (datetime(2026, 8, 31, tzinfo=UTC), 120, 7),
            (datetime(2026, 9, 1, tzinfo=UTC), 90, 3), (datetime(2026, 9, 2, tzinfo=UTC), 110, 4)]
    for ts, price, vol in days:
        bars.record_trade_bar(db, asset, price, vol, ts)
    db.commit()
    u = make_user()
    mo = u.get(f"/api/assets/{asset}/history?interval=1mo").json()
    assert [(b["open_price"], b["high_price"], b["low_price"], b["close_price"], b["volume"]) for b in mo] == [
        (100, 120, 100, 120, 12), (90, 110, 90, 110, 7)]
    wk = u.get(f"/api/assets/{asset}/history?interval=1w").json()
    assert sum(b["volume"] for b in wk) == 19 and len(wk) == 2  # Aug 30 (Sun) vs Aug 31.. (Mon week)


def test_minute_bars_are_pruned(db, asset):
    bars.record_trade_bar(db, asset, 100, 1, datetime.now(UTC) - timedelta(days=10))
    db.commit()
    assert bars.prune_minute_bars(db) >= 1
    db.commit()
    old = db.execute(text("SELECT count(*) FROM market_bars WHERE interval='1m' AND timestamp < now() - interval '8 days'")).scalar_one()
    assert old == 0


def test_volume_profile_bins_trades_by_price(make_user, asset, db):
    u = make_user()
    empty = u.get(f"/api/assets/{asset}/volume-profile").json()
    assert empty["bins"] == [] and empty["total_volume"] == 0
    for _ in range(4):
        assert u.order(asset, "buy", 5).status_code == 201
    vp = u.get(f"/api/assets/{asset}/volume-profile?bins=10").json()
    assert vp["total_volume"] == 20 and sum(b["volume"] for b in vp["bins"]) == 20
    assert all(b["low"] <= b["high"] for b in vp["bins"])
    assert vp["bins"][0]["buy_volume"] <= vp["bins"][0]["volume"]
    assert vp["poc_price"] is not None
    assert vp["current_price"] == db.get(MarketAsset, asset).current_price
