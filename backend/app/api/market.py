"""Public market data: assets, history, order book, trades, leaderboard, status."""

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.api.serialize import activity_24h, asset_out, sparklines
from app.services import live, simulator
from app.services.progress import tier_for
from app.config import get_settings
from app.db import get_db
from app.errors import AppError
from app.models import (
    Announcement,
    Channel,
    ChannelMetric,
    Job,
    MarketAsset,
    MarketBar,
    MarketEvent,
    Order,
    PriceUpdate,
    ReconciliationRun,
    Trade,
)
from app.schemas import AssetOut, BarOut, BookLevel, LeaderboardRow, MetricPoint, OrderBookOut, TradeOut
from app.security import utcnow

router = APIRouter(prefix="/api", tags=["market"])


def _listed(db: Session):
    return (
        select(MarketAsset, Channel)
        .join(Channel, Channel.id == MarketAsset.channel_id)
        .where(Channel.listing_status == "listed")
    )


@router.get("/assets", response_model=list[AssetOut])
def list_assets(
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, max_length=100),
    status: Literal["active", "halted"] | None = None,
    source: Literal["youtube_api", "demo"] | None = None,
    sort: Literal["symbol", "change", "volume", "price", "turnover", "market_cap"] = "symbol",
):
    stmt = _listed(db)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.where(or_(MarketAsset.symbol.ilike(like), Channel.title.ilike(like)))
    if status:
        stmt = stmt.where(MarketAsset.trading_status == status)
    if source:
        stmt = stmt.where(Channel.data_source == source)
    rows = db.execute(stmt.order_by(MarketAsset.symbol)).all()
    act = activity_24h(db, [a.id for a, _ in rows])
    spark = sparklines(db, [a.id for a, _ in rows])
    out = [asset_out(a, c, act.get(a.id, (0, 0, 0)), spark.get(a.id)) for a, c in rows]
    if sort == "change":
        out.sort(key=lambda x: x.change_pct, reverse=True)
    elif sort == "volume":
        out.sort(key=lambda x: x.volume_24h, reverse=True)
    elif sort == "price":
        out.sort(key=lambda x: x.current_price, reverse=True)
    elif sort == "turnover":
        out.sort(key=lambda x: x.turnover_24h, reverse=True)
    elif sort == "market_cap":
        out.sort(key=lambda x: x.market_cap, reverse=True)
    return out


def _get_asset(db: Session, asset_id: int) -> tuple[MarketAsset, Channel]:
    row = db.execute(_listed(db).where(MarketAsset.id == asset_id)).first()
    if row is None:
        raise AppError(404, "ASSET_NOT_FOUND", "Asset not found")
    return row[0], row[1]


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    a, c = _get_asset(db, asset_id)
    return asset_out(a, c, activity_24h(db, [a.id]).get(a.id, (0, 0, 0)))


@router.get("/assets/{asset_id}/live")
def asset_live(asset_id: int, db: Session = Depends(get_db)):
    """Polled every 2s by the asset page: exact in-game activity + sampled YouTube rates."""
    a, c = _get_asset(db, asset_id)
    return live.snapshot(db, a, c)


# Stored intervals are read directly; the others are rolled up from a finer stored one.
_ROLLUPS = {
    "5m": ("1m", "date_trunc('hour', timestamp) + floor(extract(minute from timestamp) / 5) * interval '5 minutes'"),
    "3m": ("1m", "date_trunc('hour', timestamp) + floor(extract(minute from timestamp) / 3) * interval '3 minutes'"),
    "15m": ("1m", "date_trunc('hour', timestamp) + floor(extract(minute from timestamp) / 15) * interval '15 minutes'"),
    "30m": ("1m", "date_trunc('hour', timestamp) + floor(extract(minute from timestamp) / 30) * interval '30 minutes'"),
    "1w": ("1d", "date_trunc('week', timestamp)"),
    "1mo": ("1d", "date_trunc('month', timestamp)"),
}


@router.get("/assets/{asset_id}/history", response_model=list[BarOut])
def history(asset_id: int,
            interval: Literal["1s", "5s", "10s", "30s", "1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"] = "1d",
            limit: int = Query(default=90, ge=1, le=1000), db: Session = Depends(get_db)):
    """OHLCV candles: 초봉(1s/5s/10s/30s), 분봉(1m/3m/5m/15m/30m), 시간봉, 일봉, 주봉, 월봉."""
    a, _ = _get_asset(db, asset_id)
    if interval.endswith("s"):
        return _second_candles(db, a, int(interval[:-1]), min(limit, 300))
    if interval in _ROLLUPS:
        base, bucket_sql = _ROLLUPS[interval]
        rows = db.execute(text(
            f"SELECT b AS timestamp, (array_agg(open_price ORDER BY timestamp))[1] AS open_price, "
            f"max(high_price) AS high_price, min(low_price) AS low_price, "
            f"(array_agg(close_price ORDER BY timestamp DESC))[1] AS close_price, sum(volume) AS volume "
            f"FROM (SELECT *, {bucket_sql} AS b FROM market_bars WHERE asset_id = :a AND interval = :i) x "
            f"GROUP BY b ORDER BY b DESC LIMIT :l"), {"a": asset_id, "i": base, "l": limit}).mappings().all()
        return [BarOut(**{**r, "volume": int(r["volume"])}) for r in reversed(rows)]
    bars = db.execute(
        select(MarketBar).where(MarketBar.asset_id == asset_id, MarketBar.interval == interval)
        .order_by(MarketBar.timestamp.desc()).limit(limit)
    ).scalars().all()
    return list(reversed(bars))


def _second_candles(db: Session, asset: MarketAsset, seconds: int, limit: int) -> list[BarOut]:
    """초봉 straight from executions (no stored bars). Seconds without a trade repeat the
    previous close with zero volume so the chart stays continuous."""
    now = utcnow()
    end = now.replace(microsecond=0)
    end = end - timedelta(seconds=end.second % seconds)
    start = end - timedelta(seconds=seconds * (limit - 1))
    rows = db.execute(text(
        "SELECT date_bin(make_interval(secs => :n), executed_at, :origin) AS b, "
        "(array_agg(execution_price ORDER BY executed_at, id))[1] AS o, max(execution_price) AS h, "
        "min(execution_price) AS l, (array_agg(execution_price ORDER BY executed_at DESC, id DESC))[1] AS c, "
        "sum(quantity) AS v FROM trades WHERE asset_id = :a AND executed_at >= :s "
        "GROUP BY b ORDER BY b"), {"n": seconds, "origin": start, "a": asset.id, "s": start}).all()
    by_bucket = {r.b: r for r in rows}
    prev = db.execute(select(Trade.execution_price).where(Trade.asset_id == asset.id, Trade.executed_at < start)
                      .order_by(Trade.executed_at.desc(), Trade.id.desc()).limit(1)).scalar()
    last = prev if prev is not None else (rows[0].o if rows else asset.current_price)
    out = []
    for i in range(limit):
        t = start + timedelta(seconds=seconds * i)
        r = by_bucket.get(t)
        if r is not None:
            out.append(BarOut(timestamp=t, open_price=r.o, high_price=r.h, low_price=r.l, close_price=r.c,
                              volume=int(r.v)))
            last = r.c
        else:
            out.append(BarOut(timestamp=t, open_price=last, high_price=last, low_price=last, close_price=last, volume=0))
    return out


@router.get("/assets/{asset_id}/volume-profile")
def volume_profile(asset_id: int, since: datetime | None = None, bins: int = Query(default=24, ge=4, le=60),
                   db: Session = Depends(get_db)):
    """매물대: traded volume per price band since `since` (default 30 days), split by aggressor side."""
    a, _ = _get_asset(db, asset_id)
    since = since or utcnow() - timedelta(days=30)
    lo, hi = db.execute(select(func.min(Trade.execution_price), func.max(Trade.execution_price))
                        .where(Trade.asset_id == asset_id, Trade.executed_at >= since)).one()
    if lo is None:
        return {"bins": [], "poc_price": None, "current_price": a.current_price, "total_volume": 0}
    width = max(1, -(-(hi - lo + 1) // bins))
    rows = db.execute(text(
        "SELECT (execution_price - :lo) / :w AS k, sum(quantity) AS v, "
        "sum(quantity) FILTER (WHERE taker_side = 'buy') AS bv FROM trades "
        "WHERE asset_id = :a AND executed_at >= :s GROUP BY k ORDER BY k"),
        {"lo": lo, "w": width, "a": asset_id, "s": since}).all()
    out = [{"low": lo + int(k) * width, "high": lo + (int(k) + 1) * width - 1, "volume": int(v),
            "buy_volume": int(bv or 0)} for k, v, bv in rows]
    poc = max(out, key=lambda b: b["volume"])
    return {"bins": out, "poc_price": (poc["low"] + poc["high"]) // 2, "current_price": a.current_price,
            "total_volume": sum(b["volume"] for b in out)}


@router.get("/assets/{asset_id}/trades", response_model=list[TradeOut])
def asset_trades(asset_id: int, limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    _get_asset(db, asset_id)
    # Counterparty identities are intentionally not exposed.
    return db.execute(
        select(Trade).where(Trade.asset_id == asset_id).order_by(Trade.id.desc()).limit(limit)
    ).scalars().all()


@router.get("/assets/{asset_id}/orderbook", response_model=OrderBookOut)
def orderbook(asset_id: int, depth: int = Query(default=10, ge=1, le=50), db: Session = Depends(get_db)):
    _get_asset(db, asset_id)

    def side(s: str, desc: bool):
        price = Order.limit_price
        return [
            BookLevel(price=p, quantity=int(q), orders=n)
            for p, q, n in db.execute(
                select(price, func.sum(Order.remaining_quantity), func.count(Order.id))
                .where(Order.asset_id == asset_id, Order.side == s,
                       Order.status.in_(("open", "partially_filled")))
                .group_by(price).order_by(price.desc() if desc else price.asc()).limit(depth)
            ).all()
        ]

    return OrderBookOut(asset_id=asset_id, bids=side("buy", True), asks=side("sell", False))


@router.get("/bots/league")
def bot_league_table(db: Session = Depends(get_db)):
    """봇 리그: the virtual traders ranked by return, their rewards and learned strategies."""
    from app.services import bot_league

    return bot_league.standings(db)


@router.get("/assets/{asset_id}/flows")
def flows(asset_id: int, days: int = Query(default=5, ge=1, le=20), db: Session = Depends(get_db)):
    """수급: net buying (shares and credits) by investor group per Korean trading day.
    개인 = players and retail-style traders, 기관 / 외국인 = those desks, 세력 = whale
    campaigns; the market maker's own side is left out (it is the other side of everything)."""
    from app.services.bots import FLOW_GROUP, PERSONAS

    a, _ = _get_asset(db, asset_id)
    kind_of = {p.username.lower(): p.kind for p in PERSONAS}
    groups = {name: FLOW_GROUP.get(kind, "개인") for name, kind in kind_of.items()}
    since = utcnow() - timedelta(days=days)
    rows = db.execute(text(
        "SELECT (t.executed_at AT TIME ZONE 'Asia/Seoul')::date AS d, u.normalized_username, u.role, "
        "       sum(CASE WHEN t.buyer_id = u.id THEN t.quantity ELSE -t.quantity END) AS q, "
        "       sum(CASE WHEN t.buyer_id = u.id THEN t.quantity ELSE -t.quantity END * t.execution_price) AS v "
        "FROM trades t JOIN users u ON u.id IN (t.buyer_id, t.seller_id) "
        "WHERE t.asset_id = :a AND t.executed_at >= :s GROUP BY 1, 2, 3"), {"a": a.id, "s": since}).all()
    order = ["개인", "외국인", "기관", "세력"]
    by_day: dict = {}
    for d, name, role, q, v in rows:
        if role == "system":
            continue
        g = groups.get(name, "개인") if role == "bot" else "개인"
        day = by_day.setdefault(d.isoformat(), {k: {"qty": 0, "value": 0} for k in order})
        day[g]["qty"] += int(q)
        day[g]["value"] += int(v)
    days_out = [{"date": d, **by_day[d]} for d in sorted(by_day, reverse=True)]
    total = {k: {"qty": sum(x[k]["qty"] for x in days_out), "value": sum(x[k]["value"] for x in days_out)} for k in order}
    return {"groups": order, "days": days_out, "total": total}


@router.get("/assets/{asset_id}/growth")
def growth(asset_id: int, db: Session = Depends(get_db)):
    """Subscriber and view changes over the last 1/7/28 days. Uses 5-minute live samples and
    daily snapshots together; when a channel has less history than the window (e.g. listed
    today), the change since the oldest record is returned with the span it actually covers,
    instead of no answer at all."""
    from app.models import ChannelSample

    a, c = _get_asset(db, asset_id)
    now = utcnow()
    points: dict[str, list[tuple[datetime, int]]] = {"subscriber_count": [], "view_count": []}
    for t, subs, views in db.execute(select(ChannelSample.observed_at, ChannelSample.subscriber_count,
                                            ChannelSample.view_count).where(
        ChannelSample.channel_id == c.id, ChannelSample.observed_at >= now - timedelta(days=30))).all():
        if subs is not None:
            points["subscriber_count"].append((t, int(subs)))
        if views is not None:
            points["view_count"].append((t, int(views)))
    for t, kind, v in db.execute(select(ChannelMetric.observed_at, ChannelMetric.metric_type, ChannelMetric.metric_value)
                                 .where(ChannelMetric.channel_id == c.id, ChannelMetric.source == c.data_source,
                                        ChannelMetric.metric_type.in_(("subscriber_count", "view_count")),
                                        ChannelMetric.observed_at >= now - timedelta(days=30))).all():
        points[kind].append((t, int(v)))

    def windows(series: list[tuple[datetime, int]]) -> dict | None:
        if not series:
            return None
        series.sort()
        last_t, last_v = series[-1]
        out = {"current": last_v, "as_of": last_t}
        for days in (1, 7, 28):
            cutoff = now - timedelta(days=days)
            base = next(((t, v) for t, v in reversed(series) if t <= cutoff), None) or series[0]
            span_h = round((last_t - base[0]).total_seconds() / 3600, 1)
            if span_h <= 0:
                out[f"d{days}"] = None
                continue
            out[f"d{days}"] = {"change": last_v - base[1], "pct": round((last_v - base[1]) / base[1] * 100, 3) if base[1] else None,
                               "span_hours": span_h, "full": base[0] <= cutoff}
        return out

    return {"hidden_subscriber_count": c.hidden_subscriber_count, "subscribers": windows(points["subscriber_count"]),
            "views": windows(points["view_count"])}


@router.get("/assets/{asset_id}/metrics", response_model=list[MetricPoint])
def metrics(asset_id: int, days: int = Query(default=60, ge=1, le=365), db: Session = Depends(get_db)):
    a, c = _get_asset(db, asset_id)
    since = (utcnow() - timedelta(days=days)).date()
    rows = db.execute(
        select(ChannelMetric.observed_date, ChannelMetric.metric_type, ChannelMetric.metric_value,
               ChannelMetric.source)
        .where(ChannelMetric.channel_id == c.id, ChannelMetric.source == c.data_source,
               ChannelMetric.observed_date >= since)
        .order_by(ChannelMetric.observed_date)
    ).all()
    by_day: dict = {}
    for d, t, v, src in rows:
        p = by_day.setdefault(d, {"observed_date": d.isoformat(), "subscriber_count": None,
                                  "view_count": None, "video_count": None, "source": src})
        p[t] = v
    return [MetricPoint(**p) for p in by_day.values()]


@router.get("/assets/{asset_id}/events")
def events(asset_id: int, db: Session = Depends(get_db)):
    _get_asset(db, asset_id)
    rows = db.execute(
        select(MarketEvent).where(MarketEvent.asset_id == asset_id)
        .order_by(MarketEvent.observed_at.desc()).limit(30)
    ).scalars().all()
    now = utcnow()
    return [{"id": e.id, "event_type": e.event_type, "event_score": float(e.event_score),
             "source": e.source, "observed_at": e.observed_at, "expires_at": e.expires_at,
             "active": e.observed_at <= now < e.expires_at, "details": e.details_json} for e in rows]


@router.get("/assets/{asset_id}/price-updates")
def price_updates(asset_id: int, db: Session = Depends(get_db)):
    """Transparent pricing: every daily update with its inputs and formula version."""
    _get_asset(db, asset_id)
    rows = db.execute(
        select(PriceUpdate).where(PriceUpdate.asset_id == asset_id)
        .order_by(PriceUpdate.effective_date.desc()).limit(30)
    ).scalars().all()
    return [{"effective_date": r.effective_date, "status": r.status, "old_price": r.old_price,
             "new_price": r.new_price, "formula_version": r.formula_version,
             "fundamental_return": r.fundamental_return, "flow_return": r.flow_return,
             "sentiment_return": r.sentiment_return, "daily_return": r.daily_return,
             "error": r.error} for r in rows]


@router.get("/market/summary")
def market_summary(db: Session = Depends(get_db)):
    assets = list_assets(db=db, q=None, status=None, source=None, sort="symbol")
    recent = db.execute(select(Trade).order_by(Trade.id.desc()).limit(15)).scalars().all()
    symbols = {a.id: a.symbol for a in assets}
    return {
        "popular": sorted(assets, key=lambda a: a.volume_24h, reverse=True)[:5],
        "gainers": sorted(assets, key=lambda a: a.change_pct, reverse=True)[:5],
        "losers": sorted(assets, key=lambda a: a.change_pct)[:5],
        "recent_trades": [
            {**TradeOut.model_validate(t).model_dump(), "symbol": symbols.get(t.asset_id)} for t in recent
        ],
        "last_price_update_at": max((a.price_updated_at for a in assets if a.price_updated_at), default=None),
        "last_data_update_at": max((a.channel.last_data_update_at for a in assets
                                    if a.channel.last_data_update_at), default=None),
    }


RANK_KEYS = {
    "turnover": (lambda a: a.turnover_24h, True),
    "market_cap": (lambda a: a.market_cap, True),
    "volume": (lambda a: a.volume_24h, True),
    "gainers": (lambda a: a.change_pct, True),
    "losers": (lambda a: a.change_pct, False),
    "popularity": (lambda a: a.popularity_score or 0, True),
}


@router.get("/rankings", response_model=list[AssetOut])
def rankings(metric: Literal["turnover", "market_cap", "volume", "gainers", "losers", "popularity"] = "turnover",
             limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)):
    """Market rankings: 거래대금 / 시가총액 / 거래량 / 상승률 / 하락률 / 인기도."""
    assets = list_assets(db=db, q=None, status=None, source=None, sort="symbol")
    key, desc = RANK_KEYS[metric]
    return sorted(assets, key=key, reverse=desc)[:limit]


EXPERT_POOL = 30  # top users by total equity considered "초고수"
EXPERT_MIN_TRADES = 5
EXPERT_MIN_BUYERS = 2  # an asset is only shown when at least 2 experts net-bought it (k-anonymity)


@router.get("/rankings/expert-picks")
def expert_picks(days: int = Query(default=7, ge=1, le=30), limit: int = Query(default=20, ge=1, le=50),
                 db: Session = Depends(get_db)):
    """초고수 PICK: assets most net-bought by the top-equity users (with real trading history)
    over the window. Only aggregates are returned; individual experts are never revealed."""
    since = utcnow() - timedelta(days=days)
    rows = db.execute(text(
        """
        WITH equity AS (
            SELECT u.id, a.cash_balance + COALESCE(SUM(p.quantity * m.current_price), 0) AS eq
            FROM users u JOIN accounts a ON a.user_id = u.id
            LEFT JOIN positions p ON p.user_id = u.id AND p.quantity > 0
            LEFT JOIN market_assets m ON m.id = p.asset_id
            WHERE u.role = 'user' AND u.status = 'active'
              AND (SELECT COUNT(*) FROM trades t WHERE t.buyer_id = u.id OR t.seller_id = u.id) >= :min_trades
            GROUP BY u.id, a.cash_balance
            ORDER BY eq DESC LIMIT :pool
        ),
        flows AS (
            SELECT t.asset_id, e.id AS uid,
                   SUM(CASE WHEN t.buyer_id = e.id THEN t.quantity * t.execution_price
                            ELSE -t.quantity * t.execution_price END) AS net
            FROM trades t JOIN equity e ON e.id IN (t.buyer_id, t.seller_id)
            WHERE t.executed_at >= :since
            GROUP BY t.asset_id, e.id
        )
        SELECT asset_id, COUNT(*) AS buyers, SUM(net) AS net_buy
        FROM flows WHERE net > 0
        GROUP BY asset_id HAVING COUNT(*) >= :min_buyers
        ORDER BY buyers DESC, net_buy DESC LIMIT :limit
        """
    ), {"min_trades": EXPERT_MIN_TRADES, "pool": EXPERT_POOL, "since": since,
        "min_buyers": EXPERT_MIN_BUYERS, "limit": limit}).all()
    if not rows:
        return []
    ids = [r.asset_id for r in rows]
    assets = {a.id: (a, c) for a, c in db.execute(_listed(db).where(MarketAsset.id.in_(ids))).all()}
    act = activity_24h(db, ids)
    out = []
    for rank, r in enumerate((r for r in rows if r.asset_id in assets), start=1):
        a, c = assets[r.asset_id]
        out.append({"rank": rank, "expert_buyers": r.buyers, "net_buy_notional": int(r.net_buy),
                    "asset": asset_out(a, c, act.get(a.id, (0, 0, 0)))})
    return out


@router.get("/leaderboard", response_model=list[LeaderboardRow])
def leaderboard(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    """Equity = cash (incl. reserved) + Σ quantity × current market price, computed server-side."""
    grant = get_settings().signup_grant
    rows = db.execute(text(
        "SELECT u.username, a.cash_balance, "
        "COALESCE(SUM(p.quantity * m.current_price), 0) AS pv "
        "FROM users u JOIN accounts a ON a.user_id = u.id "
        "LEFT JOIN positions p ON p.user_id = u.id AND p.quantity > 0 "
        "LEFT JOIN market_assets m ON m.id = p.asset_id "
        "WHERE u.role = 'user' AND u.status <> 'suspended' "
        "GROUP BY u.id, u.username, a.cash_balance "
        "ORDER BY a.cash_balance + COALESCE(SUM(p.quantity * m.current_price), 0) DESC, u.id ASC "
        "LIMIT :l"
    ), {"l": limit}).all()
    return [
        LeaderboardRow(rank=i + 1, username=u, cash_balance=c, positions_value=int(pv),
                       total_equity=c + int(pv), return_pct=round((c + int(pv)) / grant * 100 - 100, 4),
                       tier=tier_for(c + int(pv))[0])
        for i, (u, c, pv) in enumerate(rows)
    ]


@router.get("/announcements")
def announcements(db: Session = Depends(get_db)):
    rows = db.execute(select(Announcement).order_by(Announcement.id.desc()).limit(20)).scalars().all()
    return [{"id": a.id, "title": a.title, "body": a.body, "created_at": a.created_at} for a in rows]


@router.get("/system/status")
def system_status(db: Session = Depends(get_db)):
    s = get_settings()

    def last_job(job_type: str):
        j = db.execute(
            select(Job).where(Job.job_type == job_type, Job.status.in_(("succeeded", "failed")))
            .order_by(Job.finished_at.desc().nulls_last()).limit(1)
        ).scalar_one_or_none()
        return None if j is None else {"status": j.status, "finished_at": j.finished_at,
                                       "error": (j.last_error or "")[:200] if j.status == "failed" else None}

    now = utcnow()
    channels = db.execute(select(Channel).where(Channel.listing_status == "listed")).scalars().all()
    stale = [c.id for c in channels if c.last_data_update_at is None
             or now - c.last_data_update_at > timedelta(hours=s.stale_after_hours)]
    recon = db.execute(select(ReconciliationRun).order_by(ReconciliationRun.id.desc()).limit(1)).scalar_one_or_none()
    halted = db.execute(select(func.count(MarketAsset.id)).where(MarketAsset.trading_status == "halted")).scalar_one()
    db.execute(text("SELECT 1"))
    return {
        "server_time": now,
        "database": "ok",
        "youtube_api_configured": bool(s.youtube_api_key),
        "data_sources": {
            "youtube_api": sum(1 for c in channels if c.data_source == "youtube_api"),
            "demo": sum(1 for c in channels if c.data_source == "demo"),
        },
        "stale_channels": len(stale),
        "halted_assets": halted,
        "last_jobs": {t: last_job(t) for t in ("collect_data", "price_update", "reconcile", "risk_scan", "mm_requote")},
        "last_reconciliation": None if recon is None else {"status": recon.status, "at": recon.created_at},
        "fee_bps": s.fee_bps,
        "market_activity": {
            **simulator.snapshot(),
            "trades_last_5m": db.execute(select(func.count(Trade.id)).where(
                Trade.executed_at >= now - timedelta(minutes=5))).scalar_one(),
        },
        "disclaimer": "All prices are virtual game prices. They do not represent the monetary "
                      "value or future performance of any real YouTube channel.",
    }
