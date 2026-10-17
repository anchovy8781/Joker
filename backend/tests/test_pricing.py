"""Required tests 11, 12, 13, 20 + engine components."""

import random
from datetime import timedelta
from decimal import Decimal

import httpx
from sqlalchemy import func, select

from app.models import Channel, MarketAsset, MarketEvent, PriceUpdate
from app.security import utcnow
from app.services import collector, pricing
from app.services.listing import approve_listing, register_channel
from app.services.youtube import YouTubeDataAPIProvider
from tests.conftest import give_shares, list_demo_asset


def _asset(db, aid):
    db.expire_all()
    return db.get(MarketAsset, aid)


def test_11_combined_return_respects_limits():
    rnd = random.Random(7)
    for _ in range(5000):
        f = rnd.uniform(-1, 1) * 10  # deliberately far outside component limits
        fl = rnd.uniform(-1, 1) * 10
        s = rnd.uniform(-1, 1) * 10
        raw, daily = pricing.combine(f, fl, s)
        assert -Decimal("0.08") <= daily <= Decimal("0.08")
    # Component maxima: 0.5*0.04 + 0.3*0.03 + 0.2*0.02 = 0.033 (< 8% clamp)
    raw, daily = pricing.combine(0.04, 0.03, 0.02)
    assert daily == Decimal("0.033")
    assert pricing.next_price(10_000, Decimal("0.08")) == 10_800
    assert pricing.next_price(10_000, Decimal("-0.08")) == 9_200
    assert pricing.next_price(101, Decimal("-0.08")) == 100  # floor at minimum price


def test_11b_component_returns_are_bounded():
    f = pricing.Features(has_7d=True, has_28d=True, hidden_subs=False, sub_g7=0.5, sub_g28w=-0.5,
                         view_g7=0.5, view_g28w=-0.5, view_g7_prev=0.0, confidence=1.0)
    ret, _ = pricing.fundamental_return(f, 0.0, 0.0)
    assert 0.0 < ret <= 0.04
    f.sub_g7, f.view_g7, f.sub_g28w, f.view_g28w = -0.5, -0.5, 0.5, 0.5
    ret, _ = pricing.fundamental_return(f, 0.0, 0.0)
    assert -0.04 <= ret < 0.0


def test_11c_daily_update_moves_price_within_limit(make_user, db):
    aids = [list_demo_asset(db, symbol=f"DP{i}", price=10_000) for i in range(4)]
    buyers = [make_user() for _ in range(6)]
    for b in buyers:  # strong one-sided demand on the first asset
        b.order(aids[0], "buy", 30)
    summary = pricing.run_daily_update(db, utcnow().date())
    db.commit()
    assert summary["applied"] == 4
    for aid in aids:
        a = _asset(db, aid)
        assert abs(a.reference_price / a.previous_price - 1) <= 0.08
        assert a.current_price == a.reference_price
    upd = db.execute(select(PriceUpdate).where(PriceUpdate.asset_id == aids[0])).scalar_one()
    assert float(upd.flow_return) > 0 and upd.inputs_json["flow"]["distinct_takers"] == 6
    assert upd.formula_version == pricing.FORMULA_VERSION
    assert upd.inputs_json["data_source"] == "demo"


def test_12_rerunning_price_update_does_not_double_apply(db, asset):
    day = utcnow().date()
    first = pricing.run_daily_update(db, day)
    db.commit()
    p1 = _asset(db, asset).reference_price
    for _ in range(3):
        again = pricing.run_daily_update(db, day)
        db.commit()
        assert again["applied"] == 0 and again["already_done"] == 1
    assert first["applied"] == 1
    assert _asset(db, asset).reference_price == p1
    assert db.execute(select(func.count(PriceUpdate.id))).scalar_one() == 1
    # The worker's job dedup key also prevents a second queued run.
    from app.services import jobs

    assert jobs.enqueue(db, "price_update", {}, dedup_key=f"price:{day}") is not None
    assert jobs.enqueue(db, "price_update", {}, dedup_key=f"price:{day}") is None


def test_flow_counts_each_trade_once_and_ignores_unfilled_orders(make_user, db, bare_asset):
    seller, buyer = make_user(), make_user()
    give_shares(db, seller.id, bare_asset, 20)
    # Unfilled resting orders: must not count
    buyer.order(bare_asset, "buy", 50, price=9_500)
    t0 = utcnow() - timedelta(days=1)
    ret, info = pricing.flow_return(db, bare_asset, t0, utcnow())
    # give_shares trade: user was taker on buy side
    assert info["trades"] == 1
    seller.order(bare_asset, "sell", 10, price=10_000)
    buyer.order(bare_asset, "buy", 10, price=10_000)
    mid = utcnow()
    _, a = pricing.flow_return(db, bare_asset, t0, mid)
    _, b = pricing.flow_return(db, bare_asset, mid, utcnow() + timedelta(seconds=1))
    assert a["trades"] == 2 and b["trades"] == 0  # non-overlapping windows
    assert a["buy_notional"] == 300_000 and a["sell_notional"] == 0


def test_market_maker_aggression_excluded_from_flow(make_user, db, bare_asset):
    from app.services.listing import requote_market_maker

    buyer = make_user()
    buyer.order(bare_asset, "buy", 5, price=10_300)  # rests above where the MM will quote
    requote_market_maker(db, bare_asset)  # MM sell at +0.5% crosses it => MM is the taker
    db.commit()
    _, info = pricing.flow_return(db, bare_asset, utcnow() - timedelta(hours=1), utcnow())
    assert info["trades"] == 0


def test_sentiment_events_deduplicated_and_expire(db, asset):
    a = _asset(db, asset)
    ch = db.get(Channel, a.channel_id)
    now = utcnow()
    assert "trending" in collector.detect_events(db, ch, a, now.date(), now, trending=True)
    assert collector.detect_events(db, ch, a, now.date(), now, trending=True) == []
    assert db.execute(select(func.count(MarketEvent.id)).where(MarketEvent.event_type == "trending")).scalar_one() == 1
    ret, info = pricing.sentiment_return(db, asset, now + timedelta(minutes=1))
    expected = max(-1.0, min(1.0, sum(e["score"] for e in info["events"])))
    assert info["score"] == expected and abs(ret - expected * 0.02) < 1e-12
    assert "trending" in [e["type"] for e in info["events"]]
    later, info = pricing.sentiment_return(db, asset, now + timedelta(days=8))
    assert later == 0 and info["events"] == []


def test_13_stale_data_not_presented_as_fresh(make_user, db, asset):
    a = _asset(db, asset)
    ch = db.get(Channel, a.channel_id)
    ch.last_data_update_at = utcnow() - timedelta(hours=72)
    db.commit()
    u = make_user()
    detail = u.get(f"/api/assets/{asset}").json()
    assert detail["freshness"]["status"] == "stale"
    assert detail["freshness"]["age_hours"] >= 72
    assert detail["is_demo"] is True and detail["channel"]["data_source"] == "demo"
    status = u.get("/api/system/status").json()
    assert status["stale_channels"] == 1
    # The price engine refuses stale inputs and halts trading
    old = a.reference_price
    summary = pricing.run_daily_update(db, utcnow().date())
    db.commit()
    assert summary["skipped"] == 1
    a = _asset(db, asset)
    assert a.reference_price == old and a.trading_status == "halted"
    assert a.halt_reason.startswith("AUTO:STALE_DATA")
    assert u.order(asset, "buy", 1).json()["error"]["code"] == "TRADING_HALTED"
    # Fresh data again -> automatic resume (operator halts are never auto-resumed)
    collector.collect_all(db)
    db.commit()
    assert _asset(db, asset).trading_status == "active"


def test_never_collected_real_channel_is_not_tradable(db, make_admin):
    ch = register_channel(db, youtube_channel_id="UC" + "a" * 22, title="Real channel",
                          data_source="youtube_api", category=None, actor_id=None)
    a = approve_listing(db, channel_id=ch.id, symbol="REAL", initial_price=5_000,
                        shares_outstanding=100_000, actor_id=None)
    db.commit()
    assert a.trading_status == "halted" and "AWAITING_DATA" in a.halt_reason
    admin = make_admin()
    r = admin.post(f"/api/admin/assets/{a.id}/resume", json={"reason": "try resume"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DATA_NOT_FRESH"
    detail = admin.get(f"/api/assets/{a.id}").json()
    assert detail["freshness"]["status"] == "never" and detail["is_demo"] is False


def _api_channel(db):
    ch = register_channel(db, youtube_channel_id="UC" + "b" * 22, title="Real channel B",
                          data_source="youtube_api", category=None, actor_id=None)
    db.commit()
    return ch


def _mock_api(handler):
    return YouTubeDataAPIProvider("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_youtube_api_provider_parses_official_fields(db, monkeypatch):
    ch = _api_channel(db)
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.path)
        if req.url.path.endswith("/channels"):
            assert req.url.params["part"] == "snippet,statistics"
            return httpx.Response(200, json={"items": [{
                "id": ch.youtube_channel_id,
                "snippet": {"title": "Official Title", "customUrl": "@official",
                            "thumbnails": {"default": {"url": "https://yt3.example/t.jpg"}}},
                "statistics": {"viewCount": "123456789", "subscriberCount": "45600",
                               "hiddenSubscriberCount": False, "videoCount": "321"}}]})
        return httpx.Response(200, json={"items": [{"snippet": {"channelId": ch.youtube_channel_id}}]})

    res = collector.collect_all(db, api=_mock_api(handler))
    db.commit()
    assert res["api_updated"] == 1 and res["errors"] == []
    db.refresh(ch)
    assert ch.title == "Official Title" and ch.data_quality_status == "ok"
    assert ch.last_data_update_at is not None
    assert "/youtube/v3/channels" in seen[0]


def test_20_collection_failure_keeps_price_and_status_safe(make_user, db, asset):
    """API outage: last verified data kept with its original timestamp, prices unchanged."""
    ch = _api_channel(db)
    a = approve_listing(db, channel_id=ch.id, symbol="REALB", initial_price=7_000,
                        shares_outstanding=100_000, actor_id=None)
    db.commit()
    ok = _mock_api(lambda r: httpx.Response(200, json={"items": [{
        "id": ch.youtube_channel_id, "snippet": {"title": "B"},
        "statistics": {"viewCount": "1000", "subscriberCount": "10", "videoCount": "1"}}]}))
    collector.collect_all(db, api=ok)
    db.commit()
    assert _asset(db, a.id).trading_status == "active"
    db.refresh(ch)
    verified_at = ch.last_data_update_at

    down = _mock_api(lambda r: httpx.Response(503, text="backend error"))
    res = collector.collect_all(db, api=down, now=utcnow() + timedelta(hours=1))
    db.commit()
    assert res["errors"] and "503" in res["errors"][0] and "test-key" not in res["errors"][0]
    db.refresh(ch)
    assert ch.last_data_update_at == verified_at  # not refreshed by a failed call
    assert _asset(db, a.id).reference_price == 7_000

    # Network error, much later: data now stale -> halted, price untouched
    def boom(r):
        raise httpx.ConnectError("unreachable")

    later = utcnow() + timedelta(hours=60)
    collector.collect_all(db, api=_mock_api(boom), now=later)
    db.commit()
    a2 = _asset(db, a.id)
    assert a2.trading_status == "halted" and a2.reference_price == 7_000
    # Price engine skips (keeps price) rather than computing on stale data
    pricing.run_daily_update(db, later.date(), now=later)
    db.commit()
    upd = db.execute(select(PriceUpdate).where(PriceUpdate.asset_id == a.id)).scalar_one()
    assert upd.status == "skipped" and upd.new_price == upd.old_price == 7_000
    # The demo asset is unaffected by the real API outage
    assert _asset(db, asset).trading_status in ("active", "halted")


def test_deleted_channel_marked_unavailable_and_halted(db):
    ch = _api_channel(db)
    a = approve_listing(db, channel_id=ch.id, symbol="GONE", initial_price=7_000,
                        shares_outstanding=100_000, actor_id=None)
    db.commit()
    res = collector.collect_all(db, api=_mock_api(lambda r: httpx.Response(200, json={"items": []})))
    db.commit()
    assert res["api_missing"] == [ch.youtube_channel_id]
    a = _asset(db, a.id)
    assert a.trading_status == "halted" and "DATA_UNAVAILABLE" in a.halt_reason


def test_quota_is_enforced(db, monkeypatch):
    from app.config import get_settings
    from app.services.youtube import QuotaExceeded

    monkeypatch.setattr(get_settings(), "youtube_daily_quota", 2)
    ch = _api_channel(db)
    api = _mock_api(lambda r: httpx.Response(200, json={"items": []}))
    api.fetch_channels([ch.youtube_channel_id])
    api.fetch_channels([ch.youtube_channel_id])
    try:
        api.fetch_channels([ch.youtube_channel_id])
        raise AssertionError("expected quota error")
    except QuotaExceeded:
        pass


def test_price_engine_failure_keeps_old_price(db, asset, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("feature store down")

    monkeypatch.setattr(pricing, "compute_features", broken)
    old = _asset(db, asset).reference_price
    summary = pricing.run_daily_update(db, utcnow().date())
    db.commit()
    assert summary["failed"] == 1
    assert _asset(db, asset).reference_price == old
    upd = db.execute(select(PriceUpdate)).scalar_one()
    assert upd.status == "failed" and "feature store down" in upd.error


def test_demo_and_real_data_never_mix(db, asset):
    a = _asset(db, asset)
    ch = db.get(Channel, a.channel_id)
    from app.models import ChannelMetric

    sources = set(db.execute(select(ChannelMetric.source).where(ChannelMetric.channel_id == ch.id)).scalars())
    assert sources == {"demo"}
