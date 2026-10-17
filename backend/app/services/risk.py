"""Abuse detection. Every event stores a human-readable reason plus evidence.

Response tiers (derived from the combined score of unreviewed events in the last 7 days):
  low     (< 3)   record only
  medium  (3..7)  tighter order rate limit
  high    (>= 8)  new orders held, operator review requested
  critical event  clear abuse -> account 'restricted' (reversible, appealable; never auto-ban)
No single weak signal (e.g. a shared IP) can push a user past 'low' on its own.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import MarketAsset, Order, RiskEvent, Trade, User, UserSession
from app.security import utcnow
from app.services.audit import write_audit

SEVERITY_POINTS = {"low": 1, "medium": 3, "high": 6, "critical": 10}
PAIR_TRADES_MEDIUM = 5
RELATED_PAIR_TRADES_HIGH = 3
RELATED_PAIR_TRADES_CRITICAL = 10
CANCEL_MIN_ORDERS = 20
CANCEL_RATIO = 0.9
SPOOF_MIN_EVENTS = 3
SPOOF_LIFETIME_SECONDS = 60
VOLUME_SPIKE_FACTOR = 10


def add_event(db: Session, *, user_id: int | None, asset_id: int | None, event_type: str,
              severity: str, reason: str, evidence: dict, dedup_key: str | None) -> RiskEvent | None:
    if dedup_key and db.execute(select(RiskEvent.id).where(RiskEvent.dedup_key == dedup_key)).first():
        return None
    ev = RiskEvent(user_id=user_id, asset_id=asset_id, event_type=event_type, severity=severity,
                   score=SEVERITY_POINTS[severity], reason=reason[:500], evidence_json=evidence,
                   dedup_key=dedup_key)
    try:
        with db.begin_nested():
            db.add(ev)
            db.flush()
    except IntegrityError:
        return None
    return ev


def _system_ids(db: Session) -> set[int]:
    return set(db.execute(select(User.id).where(User.role == "system")).scalars())


def related(db: Session, a: int, b: int) -> list[str]:
    """Explainable links between two accounts."""
    reasons = []
    ua, ub = db.get(User, a), db.get(User, b)
    if ua and ub and ua.signup_ip_hash and ua.signup_ip_hash == ub.signup_ip_hash:
        reasons.append("same_signup_network")
    shared = db.execute(
        select(func.count()).select_from(UserSession).where(
            UserSession.user_id == a,
            UserSession.ip_hash.in_(select(UserSession.ip_hash).where(UserSession.user_id == b)),
        )
    ).scalar_one()
    if shared:
        reasons.append("shared_login_network")
    return reasons


def check_trades(db: Session, trades: list[Trade], today: date | None = None) -> list[RiskEvent]:
    """Inline checks after execution: repeated trading between the same pair of accounts."""
    today = today or utcnow().date()
    system = _system_ids(db)
    events = []
    since = utcnow() - timedelta(hours=24)
    for pair in {tuple(sorted((t.buyer_id, t.seller_id))) for t in trades}:
        a, b = pair
        if a in system or b in system:
            continue
        n = db.execute(
            select(func.count(Trade.id)).where(
                Trade.executed_at >= since,
                or_(and_(Trade.buyer_id == a, Trade.seller_id == b),
                    and_(Trade.buyer_id == b, Trade.seller_id == a)),
            )
        ).scalar_one()
        links = related(db, a, b)
        evidence = {"counterparties": [a, b], "trades_24h": n, "links": links}
        if links and n >= RELATED_PAIR_TRADES_CRITICAL:
            sev, etype = "critical", "wash_trading_related_accounts"
            reason = f"{n} trades in 24h between linked accounts ({', '.join(links)})"
        elif links and n >= RELATED_PAIR_TRADES_HIGH:
            sev, etype = "high", "related_account_trading"
            reason = f"{n} trades in 24h between linked accounts ({', '.join(links)})"
        elif n >= PAIR_TRADES_MEDIUM:
            sev, etype = "medium", "repeated_pair_trading"
            reason = f"{n} trades in 24h between the same two accounts"
        else:
            continue
        for uid in pair:
            ev = add_event(db, user_id=uid, asset_id=trades[0].asset_id, event_type=etype, severity=sev,
                           reason=reason, evidence=evidence,
                           dedup_key=f"{etype}:{a}:{b}:{uid}:{today.isoformat()}")
            if ev:
                events.append(ev)
    for uid in {e.user_id for e in events if e.user_id}:
        recompute_user_risk(db, uid)
    return events


def scan(db: Session, now: datetime | None = None) -> dict:
    """Periodic scan: cancellation abuse, spoofing-like patterns, volume spikes, concentration."""
    now = now or utcnow()
    hour = now.replace(minute=0, second=0, microsecond=0)
    since = now - timedelta(hours=1)
    system = _system_ids(db)
    created = 0

    # 1) Excessive cancellations
    rows = db.execute(
        select(Order.user_id, func.count(Order.id),
               func.count(Order.id).filter(Order.status == "canceled",
                                           Order.status_reason == "USER_CANCELED"))
        .where(Order.created_at >= since)
        .group_by(Order.user_id)
    ).all()
    for uid, total, canceled in rows:
        if uid in system or total < CANCEL_MIN_ORDERS or canceled / total < CANCEL_RATIO:
            continue
        if add_event(db, user_id=uid, asset_id=None, event_type="excessive_cancellation",
                     severity="medium", reason=f"{canceled} of {total} orders canceled within 1h",
                     evidence={"orders_1h": total, "canceled_1h": canceled},
                     dedup_key=f"cancel:{uid}:{hour.isoformat()}"):
            created += 1
            recompute_user_risk(db, uid)

    # 2) Large orders placed and canceled quickly (spoofing pattern)
    big = db.execute(
        select(Order.user_id, Order.asset_id, func.count(Order.id))
        .join(MarketAsset, MarketAsset.id == Order.asset_id)
        .where(
            Order.created_at >= since,
            Order.status == "canceled",
            Order.status_reason == "USER_CANCELED",
            Order.remaining_quantity == Order.quantity,
            Order.updated_at - Order.created_at <= timedelta(seconds=SPOOF_LIFETIME_SECONDS),
            Order.quantity * MarketAsset.reference_price >= 100_000,
        )
        .group_by(Order.user_id, Order.asset_id)
    ).all()
    for uid, asset_id, n in big:
        if uid in system or n < SPOOF_MIN_EVENTS:
            continue
        if add_event(db, user_id=uid, asset_id=asset_id, event_type="spoofing_pattern", severity="high",
                     reason=f"{n} large orders canceled unfilled within {SPOOF_LIFETIME_SECONDS}s",
                     evidence={"orders": n, "window": "1h"},
                     dedup_key=f"spoof:{uid}:{asset_id}:{hour.isoformat()}"):
            created += 1
            recompute_user_risk(db, uid)

    # 3) Volume spikes and trade concentration per asset
    week_ago = now - timedelta(days=7)
    for asset_id, vol_1h in db.execute(
        select(Trade.asset_id, func.sum(Trade.quantity)).where(Trade.executed_at >= since)
        .group_by(Trade.asset_id)
    ).all():
        vol_7d = db.execute(
            select(func.coalesce(func.sum(Trade.quantity), 0)).where(
                Trade.asset_id == asset_id, Trade.executed_at >= week_ago, Trade.executed_at < since)
        ).scalar_one()
        avg_hourly = vol_7d / (7 * 24)
        if vol_1h >= 100 and vol_1h > VOLUME_SPIKE_FACTOR * max(avg_hourly, 1):
            if add_event(db, user_id=None, asset_id=asset_id, event_type="volume_spike", severity="low",
                         reason=f"1h volume {vol_1h} vs 7d hourly average {avg_hourly:.1f}",
                         evidence={"volume_1h": int(vol_1h), "avg_hourly_7d": avg_hourly},
                         dedup_key=f"spike:{asset_id}:{hour.isoformat()}"):
                created += 1
            # Who drove it? Concentration of non-system participation.
            per_user: dict[int, int] = {}
            for b, s_, q in db.execute(
                select(Trade.buyer_id, Trade.seller_id, Trade.quantity)
                .where(Trade.asset_id == asset_id, Trade.executed_at >= since)
            ).all():
                for u in (b, s_):
                    if u not in system:
                        per_user[u] = per_user.get(u, 0) + q
            for uid, q in per_user.items():
                share = q / vol_1h
                if share >= 0.7:
                    if add_event(db, user_id=uid, asset_id=asset_id, event_type="trade_concentration",
                                 severity="medium",
                                 reason=f"Account was party to {share:.0%} of a volume spike",
                                 evidence={"share": share, "volume_1h": int(vol_1h)},
                                 dedup_key=f"conc:{uid}:{asset_id}:{hour.isoformat()}"):
                        created += 1
                        recompute_user_risk(db, uid)
    db.flush()
    return {"created": created}


def recompute_user_risk(db: Session, user_id: int) -> str:
    user = db.get(User, user_id)
    if user is None or user.role == "system":
        return "low"
    since = utcnow() - timedelta(days=7)
    events = db.execute(
        select(RiskEvent).where(RiskEvent.user_id == user_id, RiskEvent.created_at >= since,
                                RiskEvent.reviewed_at.is_(None))
    ).scalars().all()
    score = sum(e.score for e in events)
    level = "high" if score >= 8 else "medium" if score >= 3 else "low"
    if level != user.risk_level:
        write_audit(db, "risk.level_change", target_type="user", target_id=user_id,
                    metadata={"from": user.risk_level, "to": level, "score": score,
                              "events": [e.id for e in events]})
        user.risk_level = level
    if any(e.severity == "critical" for e in events) and user.status == "active":
        user.status = "restricted"
        write_audit(db, "risk.auto_restrict", target_type="user", target_id=user_id,
                    metadata={"reason": "critical risk event", "events": [e.id for e in events
                                                                          if e.severity == "critical"]})
    return level
