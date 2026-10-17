"""Public market data: assets, history, order book, trades, leaderboard, status."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.api.serialize import activity_24h, asset_out, sparklines
from app.services import live
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


@router.get("/assets/{asset_id}/history", response_model=list[BarOut])
def history(asset_id: int, interval: Literal["1h", "1d"] = "1d",
            limit: int = Query(default=90, ge=1, le=1000), db: Session = Depends(get_db)):
    _get_asset(db, asset_id)
    bars = db.execute(
        select(MarketBar).where(MarketBar.asset_id == asset_id, MarketBar.interval == interval)
        .order_by(MarketBar.timestamp.desc()).limit(limit)
    ).scalars().all()
    return list(reversed(bars))


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
                       total_equity=c + int(pv), return_pct=round((c + int(pv)) / grant * 100 - 100, 4))
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
        "disclaimer": "All prices are virtual game prices. They do not represent the monetary "
                      "value or future performance of any real YouTube channel.",
    }
