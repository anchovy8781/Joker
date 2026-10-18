"""Virtual traders: 세력(whales), momentum chasers, contrarians and noise traders.

They are ordinary accounts with role 'bot' that trade through the same order engine,
fees and ledger as players (so reconciliation still balances). Their starting capital
is system-funded. Leaderboards and risk scans ignore them.

Behaviour per round (every few seconds):
  * noise     — small random buys/sells
  * momentum  — buy after a 10-minute rise, sell after a fall
  * contrarian— sell above the intraday fair value, buy below it
  * whale     — per asset and 90-minute window a deterministic "campaign": accumulate
                (heavy buying) for the first 55%, then distribute (heavy selling)
  * buffett   — 버핏형 value investor: buys good channels (popularity, growing audience)
                when they trade below fair value, holds, trims only when clearly overpriced
  * munger    — 멍거형 concentrated quality: only the top channels by popularity, buys dips,
                rarely trades, sells only when quality fades
  * goldman   — 골드만형 investment bank: large two-sided flow around fair value (mostly
                limit orders), arbitrages gaps and rides whale campaigns early
Orders are mostly market orders (they move the price) plus some limit orders near the
touch (they add depth). Bots never trade with themselves (self-trade prevention applies).
"""

import hashlib
import logging
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Account, Channel, LedgerEntry, MarketAsset, MarketBar, Position, Trade, User
from app.security import utcnow
from app.services import intraday
from app.services.fees import bps_down, bps_up
from app.services.ledger import lock_account, post_entry
from app.services.listing import fund_market_maker, get_market_maker, requote_market_maker
from app.services.orders import OrderRequest, place_order

log = logging.getLogger("yex.bots")


@dataclass(frozen=True)
class Persona:
    username: str
    kind: str  # whale | momentum | contrarian | noise
    capital: int
    activity: float  # chance to act on an asset in a round


PERSONAS: list[Persona] = [
    Persona("세력_큰손고래", "whale", 80_000_000, 0.65),
    Persona("세력_작전주팀", "whale", 50_000_000, 0.6),
    Persona("세력_기관창구", "whale", 120_000_000, 0.45),
    Persona("추세_불나방", "momentum", 8_000_000, 0.4),
    Persona("추세_돌파매매", "momentum", 8_000_000, 0.35),
    Persona("추세_단타왕", "momentum", 5_000_000, 0.45),
    Persona("역발상_가치투자", "contrarian", 10_000_000, 0.35),
    Persona("역발상_줍줍이", "contrarian", 6_000_000, 0.4),
    Persona("개미_퇴근길", "noise", 2_000_000, 0.35),
    Persona("개미_점심시간", "noise", 2_000_000, 0.3),
    Persona("개미_새벽감성", "noise", 1_500_000, 0.3),
    Persona("개미_존버", "noise", 3_000_000, 0.25),
    # Investor archetypes (styles, not real people or firms)
    Persona("버핏형_가치투자", "buffett", 150_000_000, 0.3),
    Persona("멍거형_집중투자", "munger", 100_000_000, 0.2),
    Persona("골드만형_투자은행", "goldman", 300_000_000, 0.7),
]
BY_NAME = {p.username: p for p in PERSONAS}
_centres: dict[int, int] = {}  # price at the last market-maker requote, per asset
WHALE_WINDOW = timedelta(minutes=90)


def ensure_bots(db: Session) -> list[User]:
    """Create missing bot accounts and fund each one once with its starting capital.
    Two queries when everything already exists (this runs every round)."""
    norms = [p.username.lower() for p in PERSONAS]
    existing = {u.normalized_username: u for u in db.execute(
        select(User).where(User.normalized_username.in_(norms))).scalars()}
    funded = set(db.execute(select(LedgerEntry.user_id).where(
        LedgerEntry.user_id.in_([u.id for u in existing.values()]),
        LedgerEntry.reference_type == "bot_seed")).scalars()) if existing else set()
    users = []
    for p, norm in zip(PERSONAS, norms):
        u = existing.get(norm)
        if u is None:
            u = User(username=p.username, normalized_username=norm, role="bot", password_hash=None,
                     verification_status="verified")
            db.add(u)
            db.flush()
            db.add(Account(user_id=u.id, cash_balance=0, reserved_cash=0))
            db.flush()
        if u.id not in funded:
            post_entry(db, lock_account(db, u.id), entry_type="system_funding", amount=p.capital,
                       transaction_id=uuid.uuid4(), reference_type="bot_seed", reference_id=u.id,
                       memo=f"Starting capital for virtual trader ({p.kind})")
            db.flush()  # the session does not autoflush: make the seed visible to the next check
            funded.add(u.id)
        users.append(u)
    return users


def _refill(db: Session, u: User, p: Persona, now: datetime) -> None:
    """A broke bot gets topped up at most once per day (keeps the market alive)."""
    acct = db.get(Account, u.id)
    if acct.cash_balance - acct.reserved_cash >= p.capital // 5:
        return
    tag = f"{u.id}:{now.date().isoformat()}"
    if db.execute(select(LedgerEntry.id).where(LedgerEntry.reference_type == "bot_refill",
                                               LedgerEntry.reference_id == tag)).first():
        return
    post_entry(db, lock_account(db, u.id), entry_type="system_funding", amount=p.capital // 2,
               transaction_id=uuid.uuid4(), reference_type="bot_refill", reference_id=tag,
               memo="Daily refill for virtual trader")


def whale_phase(asset_id: int, now: datetime) -> tuple[str | None, float]:
    """('accumulate' | 'distribute' | None, strength 0..1) — deterministic per 90-minute window."""
    secs = WHALE_WINDOW.total_seconds()
    idx = int(now.timestamp() // secs)
    h = int(hashlib.sha256(f"whale:{asset_id}:{idx}".encode()).hexdigest()[:8], 16)
    if h % 100 >= 40:  # ~40% of windows have a campaign on a given asset
        return None, 0.0
    strength = 0.4 + (h >> 8) % 60 / 100
    frac = (now.timestamp() % secs) / secs
    return ("accumulate" if frac < 0.55 else "distribute"), strength


def _ret_10m(db: Session, asset_id: int, now: datetime) -> float:
    closes = db.execute(select(MarketBar.close_price).where(
        MarketBar.asset_id == asset_id, MarketBar.interval == "1m", MarketBar.timestamp >= now - timedelta(minutes=10))
        .order_by(MarketBar.timestamp)).scalars().all()
    return (closes[-1] - closes[0]) / closes[0] if len(closes) >= 2 and closes[0] else 0.0


def _decide(p: Persona, rng: random.Random, asset: MarketAsset, ctx: dict, held: int) -> tuple[str, float] | None:
    """(side, order value in credits) or None. `held` = shares the bot can sell now."""
    price = asset.current_price
    gap = (price - ctx["fair"]) / ctx["fair"]  # >0: above fair value
    if p.kind == "noise":
        return rng.choice(("buy", "sell")), rng.uniform(15_000, 150_000)
    if p.kind == "momentum":
        r = ctx["ret_10m"]
        if abs(r) < 0.002:
            return None
        return ("buy" if r > 0 else "sell"), rng.uniform(80_000, 600_000) * min(3.0, abs(r) / 0.004)
    if p.kind == "contrarian":
        if abs(gap) < 0.025:
            return None
        return ("sell" if gap > 0 else "buy"), rng.uniform(100_000, 700_000) * min(3.0, abs(gap) / 0.02)
    if p.kind == "buffett":
        quality = (asset.popularity_score or 25) >= 22 and ctx["yt_bps"] >= -50
        if quality and gap < -0.012:  # a good business on sale
            return "buy", rng.uniform(1_000_000, 4_000_000) * min(3.0, abs(gap) / 0.02)
        if held and gap > 0.06:  # clearly overpriced: trim a little, keep the core
            return "sell", min(held, max(1, held // 5)) * price
        return None
    if p.kind == "munger":
        if asset.id not in ctx["top_quality"]:
            return ("sell", held * price) if held and (asset.popularity_score or 0) < 15 else None
        if gap <= 0.0 and rng.random() < 0.6:
            return "buy", rng.uniform(1_500_000, 5_000_000)
        return None
    if p.kind == "goldman":
        phase, _ = ctx["whale"]
        if phase == "accumulate" and ctx["ret_10m"] > 0.001:
            return "buy", rng.uniform(1_000_000, 4_000_000)
        if phase == "distribute" and held:
            return "sell", rng.uniform(1_000_000, 4_000_000)
        if abs(gap) >= 0.006:  # arbitrage back toward fair value
            return ("sell" if gap > 0 else "buy"), rng.uniform(800_000, 3_000_000)
        return rng.choice(("buy", "sell")), rng.uniform(300_000, 1_000_000)  # two-sided flow
    phase, strength = ctx["whale"]
    if phase is None:
        return (rng.choice(("buy", "sell")), rng.uniform(100_000, 500_000)) if rng.random() < 0.3 else None
    side = "buy" if phase == "accumulate" else "sell"
    if rng.random() < 0.2:  # shake-outs: a little noise against the campaign
        side = "sell" if side == "buy" else "buy"
    return side, rng.uniform(500_000, 3_500_000) * strength


# Long-term investors never flip a sell into a buy; the bank quotes passively more often.
NO_FLIP = {"buffett", "munger"}
PASSIVE_P = {"goldman": 0.6, "tape": 0.0}


def _order(db: Session, bot: User, asset: MarketAsset, side: str, value: float, rng: random.Random,
           kind: str = "noise") -> bool:
    price = max(1, asset.current_price)
    qty = max(1, int(value // price))
    if side == "sell":
        pos = db.get(Position, (bot.id, asset.id))
        free = (pos.quantity - pos.reserved_quantity) if pos else 0
        if free <= 0:
            if kind in NO_FLIP:
                return False
            # Nothing to sell yet: build a position instead (smaller), so bots always take part.
            side, qty = "buy", max(1, qty // 2)
        else:
            qty = min(qty, free)
    if side == "buy":
        acct = db.get(Account, bot.id)
        qty = min(qty, max(0, (acct.cash_balance - acct.reserved_cash) // (price * 2)))
        if qty <= 0:
            return False
    qty = min(qty, get_settings().max_order_quantity)
    if rng.random() < PASSIVE_P.get(kind, 0.3):  # passive: a limit order near the touch adds depth
        bps = rng.randint(0, 40)
        limit = bps_down(price, bps) if side == "buy" else bps_up(price, bps)
        req = OrderRequest(asset.id, side, "limit", qty, limit, f"bot:{uuid.uuid4().hex}")
    else:
        req = OrderRequest(asset.id, side, "market", qty, None, f"bot:{uuid.uuid4().hex}")
    try:
        with db.begin_nested():
            r = place_order(db, bot, req)
        return r.order.status != "rejected"
    except AppError:
        return False


def _cancel_stale_bot_orders(db: Session, bot_ids: list[int], now: datetime) -> None:
    """Passive bot quotes live ~2 minutes so the book doesn't fill with stale orders."""
    from app.models import Order
    from app.services.orders import cancel_order

    stale = db.execute(select(Order).where(Order.user_id.in_(bot_ids), Order.status.in_(("open", "partially_filled")),
                                           Order.created_at < now - timedelta(minutes=2)).limit(50)).scalars().all()
    for o in stale:
        try:
            with db.begin_nested():
                cancel_order(db, db.get(User, o.user_id), o.id)
        except AppError:
            pass


def _maybe_requote(db: Session, asset: MarketAsset, now: datetime, traded: bool, commit: bool,
                   steps: int = 1) -> None:
    """Re-centre the market maker only where the price drifted a full ladder step away from
    the last centre (or every few minutes) — requotes are the expensive part of a round."""
    stale = asset.intraday_updated_at is None or now - asset.intraday_updated_at > timedelta(minutes=3)
    last_centre = _centres.get(asset.id)
    drifted = last_centre is None or abs(asset.current_price - last_centre) * 10_000 > steps * get_settings().mm_step_bps * last_centre
    if stale or (traded and drifted):
        _centres[asset.id] = asset.current_price
        requote_market_maker(db, asset.id)
        if commit:
            db.commit()


_tape_cursor = 0


def tape_tick(db: Session, hot: list[int], now: datetime | None = None, rng: random.Random | None = None) -> dict:
    """The few-second heartbeat of the market: one small market order from a retail bot on
    each asset someone is watching plus one more in rotation, so every price keeps ticking
    even when the big personas are between their (minutes-apart) decisions. Direction leans
    toward the intraday fair value, so the tape wanders without running away."""
    global _tape_cursor
    now = now or utcnow()
    rng = rng or random.Random()
    assets = db.execute(select(MarketAsset).join(Channel, Channel.id == MarketAsset.channel_id)
                        .where(Channel.listing_status == "listed", MarketAsset.trading_status == "active")
                        .order_by(MarketAsset.id)).scalars().all()
    if not assets:
        return {"orders": 0}
    by_id = {a.id: a for a in assets}
    pick = [by_id[i] for i in hot if i in by_id][:2]
    # Watched assets every tick; the rest take turns (every third tick while someone watches),
    # keeping a tick within a few seconds even with a distant database.
    _tape_cursor += 1
    if not pick or _tape_cursor % 3 == 0:
        other = assets[(_tape_cursor // (3 if pick else 1)) % len(assets)]
        if other not in pick:
            pick.append(other)
    names = [p.username.lower() for p in PERSONAS if p.kind == "noise"]
    retail = db.execute(select(User).where(User.normalized_username.in_(names))).scalars().all()
    if not retail:
        return {"orders": 0}
    orders = 0
    for asset in pick:
        bot = rng.choice(retail)
        p = BY_NAME[bot.username]
        price = max(1, asset.current_price)
        lean = max(-0.2, min(0.2, (intraday.fair_price(asset) - price) / price * 20))
        side = "buy" if rng.random() < 0.5 + lean else "sell"
        value = get_settings().mm_level_value * rng.uniform(0.15, 1.3)
        _refill(db, bot, p, now)
        if _order(db, bot, asset, side, value, rng, "tape"):
            orders += 1
            db.commit()
            db.refresh(asset)
            # Ladder has mm_levels per side: re-centre only after a few steps of drift.
            _maybe_requote(db, asset, now, True, True, steps=3)
    db.commit()
    return {"orders": orders, "assets": len(pick)}


def run_round(db: Session, now: datetime | None = None, rng: random.Random | None = None,
              commit_each: bool = False) -> dict:
    """One market round: bots act on a sample of assets, then quotes are refreshed where
    the price moved. With commit_each the work is committed per asset so player orders
    never wait long behind a round; otherwise the caller commits."""
    s = get_settings()
    now = now or utcnow()
    rng = rng or random.Random()
    bots = ensure_bots(db)
    mm = get_market_maker(db)
    fund_market_maker(db, mm)
    if commit_each:
        db.commit()
    assets = db.execute(select(MarketAsset).join(Channel, Channel.id == MarketAsset.channel_id)
                        .where(Channel.listing_status == "listed", MarketAsset.trading_status == "active")
                        .order_by(MarketAsset.id)).scalars().all()
    if not assets:
        return {"assets": 0, "orders": 0}
    _cancel_stale_bot_orders(db, [b.id for b in bots], now)
    # 멍거형 only looks at the best few channels (highest popularity, then market cap).
    top_quality = {a.id for a in sorted(assets, key=lambda a: (a.popularity_score or 0,
                                                              a.current_price * a.shares_outstanding),
                                        reverse=True)[:3]}
    rng.shuffle(assets)
    # Each statement is a network round trip to the database; cap the round's wall time so a
    # distant database never turns the simulator into a queue in front of player requests.
    deadline = time.monotonic() + s.bot_round_budget_seconds
    orders = 0
    touched: set[int] = set()
    for asset in assets[:8]:
        ctx = {"ret_10m": _ret_10m(db, asset.id, now), "fair": max(1, intraday.fair_price(asset)),
               "whale": whale_phase(asset.id, now), "top_quality": top_quality,
               "yt_bps": (asset.intraday_detail or {}).get("yt_bps", 0)}
        for bot in rng.sample(bots, len(bots)):
            if orders >= s.bot_max_orders_per_round or time.monotonic() > deadline:
                break
            p = BY_NAME[bot.username]
            if rng.random() > p.activity:
                continue
            pos = db.get(Position, (bot.id, asset.id))
            held = (pos.quantity - pos.reserved_quantity) if pos else 0
            decision = _decide(p, rng, asset, ctx, held)
            if decision is None:
                continue
            _refill(db, bot, p, now)
            if _order(db, bot, asset, decision[0], decision[1], rng, p.kind):
                orders += 1
                touched.add(asset.id)
                if commit_each:
                    db.commit()  # release the market maker's and the asset's row locks per order
                db.refresh(asset)
        if commit_each:
            db.commit()
    for asset in assets:
        _maybe_requote(db, asset, now, asset.id in touched, commit_each)
    return {"assets": len(assets), "orders": orders, "touched": len(touched)}


def whale_net_flow(db: Session, asset_id: int, since: datetime) -> dict:
    """세력 동향: net shares bought by whale bots since `since` (for the live panel)."""
    whales = [p.username.lower() for p in PERSONAS if p.kind == "whale"]
    ids = db.execute(select(User.id).where(User.normalized_username.in_(whales))).scalars().all()
    if not ids:
        return {"net": 0, "bought": 0, "sold": 0}
    bought = db.execute(select(func.coalesce(func.sum(Trade.quantity), 0)).where(
        Trade.asset_id == asset_id, Trade.executed_at >= since, Trade.buyer_id.in_(ids))).scalar_one()
    sold = db.execute(select(func.coalesce(func.sum(Trade.quantity), 0)).where(
        Trade.asset_id == asset_id, Trade.executed_at >= since, Trade.seller_id.in_(ids))).scalar_one()
    return {"net": int(bought) - int(sold), "bought": int(bought), "sold": int(sold)}
