"""Price that moves with volume, always-on trading, virtual traders, one-tap admin, preview."""

import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from app.config import Settings, get_settings
from app.models import Account, Channel, LedgerEntry, MarketAsset, Order, Trade, User
from app.services import bots, channel_lookup, collector, reconciliation
from app.services.listing import get_market_maker, requote_market_maker
from app.services.youtube import ProviderError

CID = "UC" + "q" * 22


def _price(db, asset_id):
    a = db.get(MarketAsset, asset_id)
    db.refresh(a)
    return a.current_price


def test_defaults_keep_trading_always_open():
    s = Settings()
    assert s.market_hours_enabled is False and s.auto_halt_enabled is False and s.bots_enabled is True


def test_market_maker_quotes_a_ladder_on_both_sides(db, asset):
    mm = get_market_maker(db)
    rows = db.execute(select(Order.side, func.count()).where(
        Order.asset_id == asset, Order.user_id == mm.id, Order.status == "open").group_by(Order.side)).all()
    counts = dict(rows)
    assert counts["sell"] == get_settings().mm_levels and counts["buy"] == get_settings().mm_levels


def test_volume_moves_the_price_and_quotes_follow(make_user, asset, db):
    u = make_user()
    start = _price(db, asset)
    r = u.order(asset, "buy", 80)  # sweeps several small ask levels
    assert r.status_code == 201 and r.json()["order"]["status"] == "filled", r.text
    moved = _price(db, asset)
    assert moved > start
    requote_market_maker(db, asset)
    db.commit()
    mm = get_market_maker(db)
    best_ask = db.execute(select(func.min(Order.limit_price)).where(
        Order.asset_id == asset, Order.user_id == mm.id, Order.side == "sell", Order.status == "open")).scalar_one()
    assert best_ask > start  # the ladder re-centred on the new price instead of snapping back
    s = u.order(asset, "sell", 80)
    assert s.json()["order"]["status"] == "filled", s.text  # selling always finds a bid
    assert _price(db, asset) < moved
    assert reconciliation.reconcile(db).status == "ok"


def test_market_maker_is_funded_for_bids(db, asset):
    mm = get_market_maker(db)
    funded = db.execute(select(func.sum(LedgerEntry.amount)).where(
        LedgerEntry.user_id == mm.id, LedgerEntry.entry_type == "system_funding")).scalar_one()
    assert funded and funded >= get_settings().mm_cash_per_asset


def test_stale_data_no_longer_halts_trading(db, asset, monkeypatch):
    ch = db.get(Channel, db.get(MarketAsset, asset).channel_id)
    ch.last_data_update_at = datetime.now(UTC) - timedelta(days=10)
    ch.data_source = "youtube_api"
    db.commit()
    collector.collect_all(db, api=None)
    db.commit()
    assert db.get(MarketAsset, asset).trading_status == "active"


def test_bots_trade_and_move_prices(db, asset):
    start = _price(db, asset)
    rng = random.Random(7)
    total = 0
    prices = set()
    t0 = datetime.now(UTC)
    for i in range(12):
        out = bots.run_round(db, now=t0 + timedelta(seconds=8 * i), rng=rng)
        db.commit()
        total += out["orders"]
        prices.add(_price(db, asset))
    assert total >= 12  # about one order per round on a single asset
    assert len(prices) > 3 and prices != {start}
    bot_ids = db.execute(select(User.id).where(User.role == "bot")).scalars().all()
    names = db.execute(select(User.username).where(User.role == "bot")).scalars().all()
    assert len(bot_ids) == len(bots.PERSONAS), names
    traded = db.execute(select(func.count(Trade.id)).where(
        (Trade.buyer_id.in_(bot_ids)) | (Trade.seller_id.in_(bot_ids)))).scalar_one()
    assert traded > 0
    assert reconciliation.reconcile(db).status == "ok"


def test_bots_are_funded_once_and_hidden_from_leaderboard(db, make_user, asset):
    bots.ensure_bots(db)
    bots.ensure_bots(db)
    db.commit()
    seeds = db.execute(select(func.count(LedgerEntry.id)).where(LedgerEntry.reference_type == "bot_seed")).scalar_one()
    assert seeds == len(bots.PERSONAS)
    names = [r["username"] for r in make_user().get("/api/leaderboard").json()]
    assert not any(n.startswith(("세력_", "개미_", "추세_", "역발상_", "버핏형", "멍거형", "골드만형")) for n in names)


def test_whale_campaigns_are_random_but_stable_while_running():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    assert bots.whale_phase(1, now) == bots.whale_phase(1, now)
    phases = {bots.whale_phase(a, now)[0] for a in range(1000, 1200)}
    assert None in phases and phases & {"accumulate", "distribute"}


def test_tape_is_not_a_fixed_pattern(asset, db):
    """Two runs of the heartbeat from the same state produce different order flow."""
    a = db.get(MarketAsset, asset)
    t = datetime.now(UTC)
    runs = [[bots.tape_orders(a, a.current_price, random.Random(), t + timedelta(seconds=4 * i)) for i in range(30)]
            for _ in range(2)]
    assert runs[0] != runs[1]
    sides = [side for run in runs for tick in run for side, _ in tick]
    assert "buy" in sides and "sell" in sides


def test_live_panel_reports_whales(make_user, asset, db):
    data = make_user().get(f"/api/assets/{asset}/live").json()
    assert data["whales"]["state"] in ("매집", "분산", "관망")


def _fake_page(monkeypatch, page):
    monkeypatch.setattr(channel_lookup, "fetch_public_page", lambda path: page)


def test_listing_preview_shows_profile_before_paying(make_user, db, monkeypatch):
    _fake_page(monkeypatch, {"id": CID, "title": "미리보기 채널", "thumbnail_url": "https://yt3.ggpht.com/x.jpg",
                             "subscriber_text": "구독자 12.3만명"})
    u = make_user()
    before = u.get("/api/account").json()["cash_balance"]
    r = u.get("/api/listing-requests/preview", params={"channel": "@preview.me"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["youtube_channel_id"] == CID and p["title"] == "미리보기 채널"
    assert p["subscriber_text"] == "구독자 12.3만명" and p["listing_status"] is None
    assert p["listing_fee"] == get_settings().listing_fee
    assert u.get("/api/account").json()["cash_balance"] == before  # nothing charged


def test_handle_falls_back_to_page_when_api_finds_nothing_or_fails(monkeypatch):
    _fake_page(monkeypatch, {"id": CID, "title": "t", "thumbnail_url": None, "subscriber_text": None})

    class NoneApi:
        def resolve_handle(self, h):
            return None

    class BrokenApi:
        def resolve_handle(self, h):
            raise ProviderError("YouTube API HTTP 403")

    assert channel_lookup.resolve("@someone", NoneApi())[0] == CID
    assert channel_lookup.resolve("@someone", BrokenApi())[0] == CID


def test_admin_one_touch_listing_and_management(make_admin, make_user, db, monkeypatch):
    _fake_page(monkeypatch, {"id": CID, "title": "원터치 채널", "thumbnail_url": "https://yt3.ggpht.com/a.jpg",
                             "subscriber_text": None})
    admin = make_admin()
    r = admin.post("/api/admin/quick-list", json={"channel": "@one.touch"})
    assert r.status_code == 201, r.text
    aid = r.json()["asset_id"]
    a = db.get(MarketAsset, aid)
    assert a.trading_status == "active" and r.json()["symbol"] == "ONETOUCH"
    assert db.get(Channel, a.channel_id).thumbnail_url == "https://yt3.ggpht.com/a.jpg"
    assert admin.post("/api/admin/quick-list", json={"channel": "@one.touch"}).json()["error"]["code"] == "ALREADY_LISTED"
    # halt / resume without typing a reason
    assert admin.post(f"/api/admin/assets/{aid}/halt", json={}).status_code == 200
    db.expire_all()
    assert db.get(MarketAsset, aid).trading_status == "halted"
    assert admin.post(f"/api/admin/assets/{aid}/resume", json={}).status_code == 200
    # one-tap approve-all for pending requests
    from app.services.listing import register_channel

    for i in range(2):
        register_channel(db, youtube_channel_id=f"DEMO_bulk{i}", title=f"[DEMO] Bulk {i}", data_source="demo",
                         category=None, actor_id=None)
    db.commit()
    res = admin.post("/api/admin/channels/approve-all", json={})
    assert res.status_code == 200 and res.json()["approved"] == 2
    pending = db.execute(text("SELECT count(*) FROM channels WHERE listing_status='pending'")).scalar_one()
    assert pending == 0


def test_price_heartbeat_ticks_every_call(db, asset):
    """tape_tick: a small retail order on the watched asset each call, so the price keeps moving."""
    bots.ensure_bots(db)
    db.commit()
    rng = random.Random(3)
    t0 = datetime.now(UTC)
    prices, orders = [], 0
    for i in range(10):
        out = bots.tape_tick(db, [asset], now=t0 + timedelta(seconds=4 * i), rng=rng)
        orders += out["orders"]
        prices.append(_price(db, asset))
    assert orders >= 9
    assert len(set(prices)) > 2
    assert reconciliation.reconcile(db).status == "ok"


def test_chart_patterns_are_loose_and_can_fail(asset, db):
    """Patterns are drawn differently every time and a share of them break off early."""
    t = datetime.now(UTC)
    drawings = {tuple(bots._jittered(bots.CHART_PATTERNS["double_top"])) for _ in range(20)}
    assert len(drawings) == 20
    moods = [bots._new_mood(10_000, t, 1.0) for _ in range(400)]
    patterns = [m for m in moods if m["pattern"]]
    assert patterns and any(m["pattern"] is None for m in moods)
    assert any(m["break_at"] < 1 for m in patterns) and any(m["break_at"] > 1 for m in patterns)
    assert len({round(m["adherence"], 3) for m in patterns}) > 10


def test_fast_tape_fill_keeps_the_books_exact(db, asset):
    """The one-call heartbeat fill (ytx_tape_fill) moves cash, shares, fees, ledger rows,
    bars and the price exactly like the full engine: reconciliation stays clean."""
    bots.ensure_bots(db)
    db.commit()
    rng = random.Random(5)
    t0 = datetime.now(UTC)
    before = db.execute(select(func.count(Trade.id))).scalar_one()
    for i in range(40):
        bots.tape_tick(db, [asset], now=t0 + timedelta(seconds=4 * i), rng=rng)
    db.commit()
    trades = db.execute(select(func.count(Trade.id))).scalar_one() - before
    assert trades >= 30
    fees = db.execute(select(func.count(LedgerEntry.id)).where(LedgerEntry.entry_type == "trade_fee")).scalar_one()
    assert fees > 0
    bars = db.execute(text("SELECT count(*) FROM market_bars WHERE asset_id = :a AND interval = '1m'"),
                      {"a": asset}).scalar_one()
    assert bars >= 1
    assert reconciliation.reconcile(db).status == "ok"
