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

import logging
import math
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Account, Channel, LedgerEntry, MarketAsset, MarketBar, Position, Trade, User
from app.security import utcnow
from app.services import bot_league, intraday, krx, market_hours
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
    Persona("개미_퇴근길", "noise", 30_000_000, 0.35),
    Persona("개미_점심시간", "noise", 30_000_000, 0.3),
    Persona("개미_새벽감성", "noise", 25_000_000, 0.3),
    Persona("개미_존버", "noise", 40_000_000, 0.25),
    # Investor archetypes (styles, not real people or firms)
    Persona("버핏형_가치투자", "buffett", 150_000_000, 0.3),
    Persona("멍거형_집중투자", "munger", 100_000_000, 0.2),
    Persona("골드만형_투자은행", "goldman", 300_000_000, 0.7),
    # 기관 / 외국인 desks: trade on the heartbeat with their own slow-changing views
    Persona("기관_연기금", "institution", 400_000_000, 0.3),
    Persona("기관_자산운용", "institution", 250_000_000, 0.3),
    Persona("기관_투신", "institution", 150_000_000, 0.3),
    Persona("외국인_글로벌펀드", "foreign", 400_000_000, 0.3),
    Persona("외국인_헤지펀드", "foreign", 200_000_000, 0.3),
    Persona("외국인_패시브", "foreign", 300_000_000, 0.3),
]
# Who counts as whom in the investor-flow (수급) table.
FLOW_GROUP = {"noise": "개인", "momentum": "개인", "contrarian": "개인", "institution": "기관", "buffett": "기관",
              "munger": "기관", "goldman": "기관", "foreign": "외국인", "whale": "세력"}
BY_NAME = {p.username: p for p in PERSONAS}
_centres: dict[int, int] = {}  # price at the last market-maker requote, per asset


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
    """A broke bot gets topped up (keeps the market alive): retail bots, which carry the
    second-by-second tape, at most once per hour; the others at most once per day."""
    acct = db.get(Account, u.id)
    if acct.cash_balance - acct.reserved_cash >= p.capital // 5:
        return
    tag = f"{u.id}:{now.strftime('%Y-%m-%dT%H') if p.kind == 'noise' else now.date().isoformat()}"
    if db.execute(select(LedgerEntry.id).where(LedgerEntry.reference_type == "bot_refill",
                                               LedgerEntry.reference_id == tag)).first():
        return
    post_entry(db, lock_account(db, u.id), entry_type="system_funding", amount=p.capital // 2,
               transaction_id=uuid.uuid4(), reference_type="bot_refill", reference_id=tag,
               memo="Refill for virtual trader")


# Unpredictable by design: drawn from the OS's random source, never from the clock or ids,
# so nobody can work out from the code when a campaign starts or which way a trend runs.
_sysrand = random.SystemRandom()
_campaigns: dict[int, dict] = {}


def whale_phase(asset_id: int, now: datetime) -> tuple[str | None, float]:
    """('accumulate' | 'distribute' | None, strength 0..1). Campaigns start at random, last a
    random 20-120 minutes and follow one of several shapes (build then dump, slow
    accumulation, steady distribution), with quiet stretches of random length between."""
    c = _campaigns.get(asset_id)
    if c is None or now >= c["end"]:
        if c is not None and c["shape"] is not None or _sysrand.random() < 0.55:
            # quiet stretch
            c = {"shape": None, "end": now + timedelta(minutes=_sysrand.uniform(10, 90)), "start": now, "strength": 0.0}
        else:
            minutes = _sysrand.uniform(20, 120)
            c = {"shape": _sysrand.choice(("pump_dump", "accumulate", "distribute", "pump_dump")),
                 "start": now, "end": now + timedelta(minutes=minutes),
                 "turn": _sysrand.uniform(0.35, 0.75), "strength": _sysrand.uniform(0.35, 1.0)}
        _campaigns[asset_id] = c
    if c["shape"] is None:
        return None, 0.0
    if c["shape"] in ("accumulate", "distribute"):
        return c["shape"], c["strength"]
    frac = (now - c["start"]) / (c["end"] - c["start"])
    return ("accumulate" if frac < c["turn"] else "distribute"), c["strength"]


# Chart patterns seen again and again on coin and stock charts. Each is a path of
# (time fraction, level) keypoints; level 1.0 = one "amplitude" above the start price.
# The tape steers toward the path (plus noise), so the shapes emerge from real trades.
CHART_PATTERNS: dict[str, list[tuple[float, float]]] = {
    "double_top": [(0, 0), (0.22, 1.0), (0.38, 0.5), (0.58, 0.97), (0.78, 0.35), (1, -0.35)],          # 쌍봉
    "double_bottom": [(0, 0), (0.22, -1.0), (0.38, -0.5), (0.58, -0.97), (0.78, -0.3), (1, 0.4)],      # 쌍바닥
    "head_shoulders": [(0, 0), (0.14, 0.6), (0.26, 0.3), (0.44, 1.0), (0.6, 0.3), (0.74, 0.62),
                       (0.88, 0.22), (1, -0.4)],                                                        # 헤드앤숄더
    "inverse_head_shoulders": [(0, 0), (0.14, -0.6), (0.26, -0.3), (0.44, -1.0), (0.6, -0.3),
                               (0.74, -0.62), (0.88, -0.2), (1, 0.45)],                                 # 역헤드앤숄더
    "cup_handle": [(0, 0), (0.3, -0.65), (0.55, -0.05), (0.66, -0.25), (0.8, -0.1), (1, 0.55)],        # 컵앤핸들
    "bull_flag": [(0, 0), (0.18, 0.9), (0.32, 0.72), (0.45, 0.82), (0.58, 0.64), (0.7, 0.74),
                  (1, 1.5)],                                                                            # 상승 깃발
    "bear_flag": [(0, 0), (0.18, -0.9), (0.32, -0.72), (0.45, -0.82), (0.58, -0.64), (0.7, -0.74),
                  (1, -1.5)],                                                                           # 하락 깃발
    "ascending_triangle": [(0, 0), (0.14, 0.6), (0.27, 0.15), (0.42, 0.6), (0.55, 0.32), (0.68, 0.6),
                           (0.78, 0.47), (1, 1.2)],                                                     # 상승 삼각형 돌파
    "descending_triangle": [(0, 0), (0.14, -0.6), (0.27, -0.15), (0.42, -0.6), (0.55, -0.32),
                            (0.68, -0.6), (0.78, -0.47), (1, -1.2)],                                    # 하락 삼각형 이탈
    "pump_dump": [(0, 0), (0.12, 0.3), (0.3, 1.7), (0.38, 1.45), (0.46, 1.6), (0.62, 0.3), (1, -0.15)], # 펌프앤덤프
    "v_reversal": [(0, 0), (0.42, -1.0), (0.5, -0.9), (1, 0.25)],                                       # V자 반등
    "bull_trap": [(0, 0), (0.4, 0.45), (0.55, 0.85), (0.68, 0.1), (1, -0.55)],                          # 불트랩
    "bear_trap": [(0, 0), (0.4, -0.45), (0.55, -0.85), (0.68, -0.1), (1, 0.55)],                        # 베어트랩
    "range_chop": [(0, 0), (0.17, 0.4), (0.33, -0.35), (0.5, 0.42), (0.67, -0.38), (0.83, 0.35), (1, 0)],  # 박스권
    "staircase_up": [(0, 0), (0.2, 0.45), (0.3, 0.38), (0.5, 0.85), (0.6, 0.76), (0.8, 1.25), (1, 1.2)],  # 계단식 상승
    "staircase_down": [(0, 0), (0.2, -0.45), (0.3, -0.38), (0.5, -0.85), (0.6, -0.76), (0.8, -1.25),
                       (1, -1.2)],                                                                      # 계단식 하락
}

# Per-asset market mood for the price heartbeat. Often a chart pattern is loosely playing out
# (kind, size, tempo, how closely it is followed and whether it completes are all random and
# never shown to players); otherwise a plain random-trend regime. Volatility clusters and
# rare news shocks sit on top of either, so no pattern can be read off in advance.
_moods: dict[int, dict] = {}


def _jittered(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Same pattern, never the same drawing: nudge every turning point in time and height,
    and sometimes resolve the other way (a breakout that fails and reverses)."""
    out = [points[0]]
    for t, v in points[1:-1]:
        out.append((min(0.97, max(out[-1][0] + 0.03, t + _sysrand.uniform(-0.05, 0.05))),
                    v * _sysrand.uniform(0.75, 1.25) + _sysrand.uniform(-0.12, 0.12)))
    last = points[-1][1] * _sysrand.uniform(0.6, 1.4)
    if _sysrand.random() < 0.2:
        last = -last * _sysrand.uniform(0.3, 1.0)  # the textbook ending does not come
    out.append((1.0, last))
    return out


def _new_mood(price: int, now: datetime, vol: float) -> dict:
    base = {"start": now, "vol": vol, "progress": 0.0, "speed": 1.0, "wander": 0.0,
            "shock": 0.0, "shock_end": now, "last": now}
    if _sysrand.random() < 0.65:
        name = _sysrand.choice(list(CHART_PATTERNS))
        minutes = _sysrand.uniform(12, 70)
        return {**base, "pattern": name, "path": _jittered(CHART_PATTERNS[name]),
                "minutes": minutes, "end": now + timedelta(minutes=minutes * 1.6), "base": price,
                "amp": price * _sysrand.uniform(0.02, 0.09),
                # how closely the crowd follows the shape this time (loose ... textbook)
                "adherence": _sysrand.uniform(0.25, 1.0),
                # a third of patterns break down somewhere along the way
                "break_at": _sysrand.uniform(0.35, 0.9) if _sysrand.random() < 0.33 else 2.0,
                "drift": 0.0}
    strong = _sysrand.random() < 0.15
    drift = _sysrand.gauss(0, 0.26 if strong else 0.12)
    return {**base, "pattern": None, "drift": max(-0.32, min(0.32, drift)),
            "end": now + timedelta(minutes=_sysrand.expovariate(1 / 6))}


def _mood(asset: MarketAsset, now: datetime) -> dict:
    m = _moods.get(asset.id)
    price = max(1, asset.current_price)
    if m is None or now >= m["end"] or (m["pattern"] and m["progress"] >= min(1.0, m["break_at"])):
        # pattern finished or broke down: start something new from wherever the price is now
        m = _new_mood(price, now, (m or {}).get("vol", 1.0))
        _moods[asset.id] = m
    dt = max(0.0, (now - m["last"]).total_seconds())
    m["last"] = now
    if m["pattern"]:
        # uneven tempo: legs stretch and compress, sometimes the pattern stalls for a while
        if _sysrand.random() < 0.04:
            m["speed"] = _sysrand.choice((0.0, 0.4, 0.8, 1.0, 1.3, 1.8))
        m["progress"] = min(1.0, m["progress"] + m["speed"] * dt / (m["minutes"] * 60))
        # the target itself wanders (Ornstein-Uhlenbeck), so no two runs trace the same line
        m["wander"] += -0.08 * m["wander"] + _sysrand.gauss(0, 0.07)
    # volatility clustering: decays toward 1, jumps on random "news"
    m["vol"] = 1 + (m["vol"] - 1) * 0.93
    if _sysrand.random() < 0.03:
        m["vol"] += _sysrand.uniform(1.0, 3.5)
    # occasional news shock: a sharp one-sided burst for 20 seconds to 2 minutes
    if now >= m["shock_end"]:
        m["shock"] = 0.0
        if _sysrand.random() < 0.012:
            m["shock"] = _sysrand.choice((-1, 1)) * _sysrand.uniform(0.2, 0.4)
            m["shock_end"] = now + timedelta(seconds=_sysrand.uniform(20, 120))
            m["vol"] += _sysrand.uniform(1.0, 2.5)
    return m


def _path_at(m: dict) -> tuple[float, float]:
    """Pattern target price at the current progress and the path's steepness there."""
    f = m["progress"]
    pts = m["path"]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if f <= t1:
            k = (f - t0) / (t1 - t0) if t1 > t0 else 1.0
            level, slope = v0 + (v1 - v0) * k, (v1 - v0) / max(0.01, t1 - t0)
            return m["base"] + m["amp"] * (level + m["wander"]), slope
    return m["base"] + m["amp"] * (pts[-1][1] + m["wander"]), 0.0


def tape_orders(asset: MarketAsset, fair: int, rng: random.Random, now: datetime) -> list[tuple[str, float]]:
    """This tick's retail orders on one asset: (side, value). During a pattern the flow leans
    toward its (wandering, unevenly paced, possibly failing) path — more trades on steep legs,
    like breakout volume; otherwise a random trend regime. News shocks override both for a
    moment. Fair value only nudges; sizes are heavy-tailed."""
    m = _mood(asset, now)
    price = max(1, asset.current_price)
    gap = (fair - price) / price
    if m["pattern"] is not None:
        target, slope = _path_at(m)
        lean = max(-0.38, min(0.38, (target - price) / max(1.0, m["amp"]) * 0.6 * m["adherence"]))
        busy = 0.10 + 0.05 * min(4.0, abs(slope) * m["speed"])
    else:
        lean, busy = m["drift"], 0.12
    lean += m["shock"]
    n = 1 + sum(rng.random() < min(0.65, busy * m["vol"]) for _ in range(2))  # bursts when volatile
    out = []
    for _ in range(n):
        p_buy = 0.5 + lean + max(-0.04, min(0.04, gap * 2)) + rng.gauss(0, 0.1)
        side = "buy" if rng.random() < max(0.06, min(0.94, p_buy)) else "sell"
        value = get_settings().mm_level_value * rng.lognormvariate(-0.5, 0.8) * m["vol"] ** 0.5
        if rng.random() < 0.01:  # an occasional block trade
            value *= rng.uniform(2, 4)
        out.append((side, value))
    return out


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
    if p.kind in ("institution", "foreign"):
        return None  # these desks trade on the heartbeat (see desk_orders)
    phase, strength = ctx["whale"]
    if phase is None:
        return (rng.choice(("buy", "sell")), rng.uniform(100_000, 500_000)) if rng.random() < 0.3 else None
    side = "buy" if phase == "accumulate" else "sell"
    if rng.random() < 0.2:  # shake-outs: a little noise against the campaign
        side = "sell" if side == "buy" else "buy"
    return side, rng.uniform(500_000, 3_500_000) * strength


# Long-term investors never flip a sell into a buy; the bank quotes passively more often.
NO_FLIP = {"buffett", "munger"}
PASSIVE_P = {"goldman": 0.6, "tape": 0.0, "tape_full": 0.0}


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
        limit = krx.floor_tick(bps_down(price, bps)) if side == "buy" else krx.ceil_tick(bps_up(price, bps))
        req = OrderRequest(asset.id, side, "limit", qty, limit, f"bot:{uuid.uuid4().hex}")
    else:
        req = OrderRequest(asset.id, side, "market", qty, None, f"bot:{uuid.uuid4().hex}")
    from app import fastpath

    fastpath.wait_for_players()  # people's orders first
    try:
        with db.begin_nested():
            r = place_order(db, bot, req)
        return r.order.status not in ("rejected", "scheduled")
    except AppError:
        return False


def _fast_fill(db: Session, bot: User, asset: MarketAsset, side: str, qty: int, key: str) -> int | None:
    """Heartbeat market order against the market maker's ladder in one database call
    (ytx_tape_fill, migration 0011). Returns the fill price, 0 if the bot lacks cash or
    shares, or None when the full matching engine has to handle it."""
    from sqlalchemy import text

    from app.services.fees import fee_policy

    from app import fastpath

    s = get_settings()
    fastpath.wait_for_players()  # people's orders first
    db.flush()
    price = db.execute(text(
        "SELECT ytx_tape_fill(:a, :b, :side, :q, :fee, :base, :key, :levels)"),
        {"a": asset.id, "b": bot.id, "side": side, "q": qty,
         "fee": fee_policy(asset, utcnow()).bps, "base": s.fee_bps, "key": key, "levels": s.mm_levels}).scalar()
    if price:
        # rows changed inside the database: forget cached copies of what it touches
        # (users and channels did not change, keep those)
        from app.models import Order

        from sqlalchemy.orm.attributes import set_committed_value

        for obj in list(db.identity_map.values()):
            if isinstance(obj, (Account, Position, Order, Trade)):
                db.expire(obj)
        set_committed_value(asset, "current_price", price)  # the only asset field the tape reads
    return price


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
    # fair value moves slowly: refresh an untouched ladder every 10 minutes, not every 3
    stale = asset.intraday_updated_at is None or now - asset.intraday_updated_at > timedelta(minutes=10)
    last_centre = _centres.get(asset.id)
    drifted = last_centre is None or abs(asset.current_price - last_centre) * 10_000 > steps * get_settings().mm_step_bps * last_centre
    if stale or (traded and drifted):
        _centres[asset.id] = asset.current_price
        requote_market_maker(db, asset.id)
        if commit:
            db.commit()


_tape_cursor = 0
_desks: dict[tuple[int, str], dict] = {}
_hist: dict[int, deque] = {}  # recent (time, price) per asset, what a desk can see of the trend
_whale_flow: dict[int, float] = {}  # decaying net 세력 buying per asset, in ladder-level units (수급)
_shakeouts: dict[int, dict] = {}
_spoofs: dict[int, dict] = {}


def _trend(asset: MarketAsset, now: datetime) -> float:
    """Observed return over the last ~15 minutes (from prices the tape has seen)."""
    h = _hist.get(asset.id)
    if not h:
        return 0.0
    start = next((p for t, p in h if now - t <= timedelta(minutes=15)), h[-1][1])
    return (asset.current_price - start) / start if start else 0.0


def _signal(asset: MarketAsset, strategy: str, now: datetime) -> float:
    """What a strategy says about one stock, -1 (sell) .. +1 (buy), from public information only:
    the recent trend, the gap to fair value, and the 세력 flow the 수급 table also shows."""
    if strategy in ("trend", "contrarian"):
        x = math.tanh(_trend(asset, now) * 60)
        return x if strategy == "trend" else -x
    if strategy == "value":
        fair = max(1, intraday.fair_price(asset))
        return math.tanh((fair - asset.current_price) / fair * 25)
    return math.tanh(_whale_flow.get(asset.id, 0.0) * 0.4)


def _desk_view(asset: MarketAsset, name: str, kind: str, now: datetime) -> float:
    """A desk's current view on one stock, -1 (selling) .. +1 (buying). Each view follows one
    strategy the desk picks by what has paid it before (봇 리그); when the view ends it is graded
    by how the price moved and the desk learns from it. 기관 views last 30-120 min, 외국인 15-90."""
    d = _desks.get((asset.id, name))
    if d is not None and now >= d["end"]:
        moved = (asset.current_price - d["price"]) / d["price"] * 100 if d["price"] else 0.0
        bot_league.credit(name, kind, d["strategy"], moved * (1 if d["bias"] >= 0 else -1) * min(1.0, abs(d["bias"]) * 2))
        d = None
    if d is None:
        lo, hi = (30, 120) if kind == "institution" else (15, 90)
        strategy = bot_league.pick(name, kind, _sysrand)
        bias = 0.8 * _signal(asset, strategy, now) + _sysrand.gauss(0, 0.25)
        d = {"bias": max(-1.0, min(1.0, bias)), "strategy": strategy, "price": max(1, asset.current_price),
             "end": now + timedelta(minutes=_sysrand.uniform(lo, hi))}
        _desks[(asset.id, name)] = d
    return d["bias"]


def _minute_ago(asset_id: int, now: datetime) -> int | None:
    """The price about a minute ago, from the heartbeat's own memory (동적 VI reference)."""
    h = _hist.get(asset_id)
    return next((p for t, p in h if now - t <= timedelta(seconds=70)), None) if h else None


def _pick(group: list[User], rng: random.Random) -> User:
    """Order flow goes to bots in proportion to their conviction (봇 리그): winners trade more."""
    return rng.choices(group, weights=[bot_league.conviction(b.username) for b in group])[0]


def desk_orders(asset: MarketAsset, rng: random.Random, now: datetime,
                crowd: dict[str, list[User]] | None = None) -> list[tuple]:
    """This tick's (kind, side, value) from 기관, 외국인 and 세력. Whales accumulate or
    distribute per their campaign, and sometimes 개미털기: a sudden dump that scares retail
    out (they panic-sell with it), after which the whale buys back cheaper."""
    level = get_settings().mm_level_value
    out: list[tuple] = []
    for kind, p_act, mu in (("institution", 0.22, 0.3), ("foreign", 0.18, 0.5)):
        if rng.random() < p_act:
            group = (crowd or {}).get(kind) or []
            desk = _pick(group, rng) if group else None
            name = desk.username if desk else kind
            side = "buy" if rng.random() < 0.5 + 0.45 * _desk_view(asset, name, kind, now) else "sell"
            out.append((kind, side, level * rng.lognormvariate(mu, 0.6) * bot_league.conviction(name), desk))
    phase, strength = whale_phase(asset.id, now)
    sh = _shakeouts.get(asset.id)
    if sh and now < sh["end"]:
        if sh["stage"] == "dump":
            out += [("whale", "sell", level * rng.lognormvariate(0.9, 0.4)) for _ in range(rng.choice((1, 2)))]
        elif rng.random() < 0.6:  # buy back the shaken-out shares
            out.append(("whale", "buy", level * rng.lognormvariate(0.8, 0.4)))
    elif sh and sh["stage"] == "dump":
        _shakeouts[asset.id] = {"stage": "rebuy", "end": now + timedelta(seconds=_sysrand.uniform(40, 90))}
    elif phase == "accumulate" and _sysrand.random() < 0.003:
        _shakeouts[asset.id] = {"stage": "dump", "end": now + timedelta(seconds=_sysrand.uniform(20, 50))}
        m = _moods.get(asset.id)
        if m is not None:  # retail panics along with the dump
            m["shock"], m["shock_end"] = -0.35, _shakeouts[asset.id]["end"]
    elif phase is not None and rng.random() < 0.25:
        out.append(("whale", "buy" if phase == "accumulate" else "sell", level * rng.lognormvariate(0.5, 0.5) * strength))
    return out


def _spoof(db: Session, asset: MarketAsset, whales: list[User], now: datetime) -> None:
    """허매수: while distributing, a whale props up a big bid under the price to make the
    stock look supported — and pulls it again before it can fill. Retail leans in meanwhile."""
    from app.services.orders import cancel_order

    sp = _spoofs.get(asset.id)
    if sp and now >= sp["end"]:
        try:
            cancel_order(db, db.get(User, sp["user_id"]), sp["order_id"], reason="BOT_WITHDRAWN")
            db.commit()
        except AppError:
            db.rollback()
        _spoofs.pop(asset.id, None)
        return
    if sp or not whales or whale_phase(asset.id, now)[0] != "distribute" or _sysrand.random() > 0.012:
        return
    whale = _sysrand.choice(whales)
    price = krx.floor_tick(bps_down(max(1, asset.current_price), _sysrand.randint(50, 120)))
    qty = max(1, int(get_settings().mm_level_value * _sysrand.uniform(3, 6) // price))
    try:
        with db.begin_nested():
            r = place_order(db, whale, OrderRequest(asset.id, "buy", "limit", qty, price, f"bot:{uuid.uuid4().hex}"))
        db.commit()
    except AppError:
        db.rollback()
        return
    if r.order.status in ("open", "partially_filled"):
        end = now + timedelta(seconds=_sysrand.uniform(20, 60))
        _spoofs[asset.id] = {"order_id": r.order.id, "user_id": whale.id, "end": end}
        m = _moods.get(asset.id)
        if m is not None:
            m["shock"], m["shock_end"] = 0.15, end


def _tape_asset(db: Session, asset: MarketAsset, crowd: dict[str, list[User]], rng: random.Random, now: datetime,
                held: dict[tuple[int, int], int], cash: dict[int, int], max_orders: int = 3) -> int:
    """This tick's orders on one asset — retail flow plus 기관/외국인/세력 desks; returns how
    many went through. `held` and `cash` are this tick's snapshot of the bots' free shares
    and cash (read once per tick, then adjusted locally) so an order costs one database call."""
    orders = 0
    if asset.vi_until is not None:  # 변동성완화장치: no continuous trading until the auction is crossed
        if krx.vi_due(asset, now):
            from app.services.orders import lock_asset

            locked = lock_asset(db, asset.id)
            if krx.vi_due(locked, now):
                krx.end_vi(db, locked, now)
            db.commit()
            _maybe_requote(db, locked, now, True, True, steps=0)
        return 0
    _spoof(db, asset, crowd.get("whale", []), now)
    price = max(1, asset.current_price)
    h = _hist.setdefault(asset.id, deque(maxlen=400))
    if not h or now - h[-1][0] >= timedelta(seconds=10):
        h.append((now, price))
    _whale_flow[asset.id] = _whale_flow.get(asset.id, 0.0) * 0.97
    flow = [("noise", side, value) for side, value in tape_orders(asset, intraday.fair_price(asset), rng, now)[:max_orders]]
    flow += desk_orders(asset, rng, now, crowd)
    for kind, side, value, *chosen in flow:
        group = crowd.get(kind) or []
        if not group:
            continue
        desk = chosen[0] if chosen else None
        if desk is not None:  # a desk acts on its own view: it sells only what it holds
            if side == "sell" and held.get((desk.id, asset.id), 0) <= 0:
                continue
            bot = desk
        else:
            # A sell comes from someone who actually holds shares, so selling pressure is real.
            sellers = [b for b in group if held.get((b.id, asset.id), 0) > 0]
            if side == "sell" and not sellers:
                if kind == "whale":
                    continue  # nothing to dump yet
                side = "buy"
            bot = _pick(sellers, rng) if side == "sell" else _pick(group, rng)
        if side == "sell":
            qty = min(held[(bot.id, asset.id)], max(1, int(value // price)))
        else:
            if cash.get(bot.id, 0) < get_settings().mm_level_value:
                _refill(db, bot, BY_NAME[bot.username], now)
                db.commit()
                acct = db.get(Account, bot.id)
                cash[bot.id] = acct.cash_balance - acct.reserved_cash
            qty = min(max(1, int(value // price)), max(0, cash.get(bot.id, 0) // (price * 2)))
        qty = min(qty, get_settings().max_order_quantity)
        if qty <= 0:
            continue
        key = f"bot:{uuid.uuid4().hex}"
        filled = _fast_fill(db, bot, asset, side, qty, key)
        if filled is None:  # someone else's order is at the top of the book: full engine
            ok = _order(db, bot, asset, side, qty * price, rng, "tape_full")
        else:
            ok = filled > 0
        # Commit every fill: the next wait_for_players() must not hold the asset and market-maker
        # rows that a person's order needs.
        db.commit()
        if ok and krx.would_trigger(asset, now, _minute_ago(asset.id, now)):
            from app.services.orders import lock_asset

            locked = lock_asset(db, asset.id)
            krx.check_vi(db, locked, now, _minute_ago(asset.id, now))
            db.commit()
            if locked.vi_until is not None:
                return orders + 1  # VI: the rest of this tick's flow waits for the auction
        if ok:
            orders += 1
            if kind == "whale":
                _whale_flow[asset.id] = _whale_flow.get(asset.id, 0.0) + (1 if side == "buy" else -1) * qty * price / get_settings().mm_level_value
            if side == "sell":
                held[(bot.id, asset.id)] -= qty
            else:
                cash[bot.id] = cash.get(bot.id, 0) - qty * price
                held[(bot.id, asset.id)] = held.get((bot.id, asset.id), 0) + qty
        price = max(1, asset.current_price)
    if orders:
        # Ladder has mm_levels per side: re-centre only after a few steps of drift.
        _maybe_requote(db, asset, now, True, True, steps=3)
    return orders


def tape_tick(db: Session, hot: list[int], now: datetime | None = None, rng: random.Random | None = None) -> dict:
    """The few-second heartbeat of the market. Every listed asset keeps trading whether or
    not anyone is looking at it: each tick serves the assets someone is watching plus the
    next ones in rotation (bot_tape_rotation; by default all of them). Fills go through
    ytx_tape_fill — one database call each — so a tick over ten assets takes a couple of
    seconds even with a distant database. See tape_orders for direction and size."""
    global _tape_cursor
    now = now or utcnow()
    if not market_hours.is_open(now):  # after hours players can only reserve; bots wait too
        return {"orders": 0, "closed": True}
    rng = rng or random.Random()
    assets = db.execute(select(MarketAsset).join(Channel, Channel.id == MarketAsset.channel_id)
                        .where(Channel.listing_status == "listed", MarketAsset.trading_status == "active")
                        .order_by(MarketAsset.id)).scalars().all()
    if not assets:
        return {"orders": 0}
    by_id = {a.id: a for a in assets}
    watched = [by_id[i] for i in hot if i in by_id][:2]
    others = [a for a in assets if a not in watched]
    k = min(len(others), max(1, get_settings().bot_tape_rotation))
    turn = [others[(_tape_cursor + j) % len(others)] for j in range(k)] if others else []
    _tape_cursor = (_tape_cursor + k) % max(1, len(others))
    kinds = ("noise", "institution", "foreign", "whale")
    crowd: dict[str, list[User]] = {k: [] for k in kinds}
    for u in db.execute(select(User).where(User.normalized_username.in_(
            [p.username.lower() for p in PERSONAS if p.kind in kinds]))).scalars():
        crowd[BY_NAME[u.username].kind].append(u)
    if not crowd["noise"]:
        return {"orders": 0}
    ids = [b.id for group in crowd.values() for b in group]
    held = {(p.user_id, p.asset_id): p.quantity - p.reserved_quantity
            for p in db.execute(select(Position).where(Position.user_id.in_(ids))).scalars()}
    cash = {a.user_id: a.cash_balance - a.reserved_cash
            for a in db.execute(select(Account).where(Account.user_id.in_(ids))).scalars()}
    orders = 0
    # Watched assets always trade; the others share a time budget so a tick stays short however
    # many assets are listed — whoever does not fit continues next tick (the cursor only moves
    # past assets that actually traded).
    deadline = time.monotonic() + get_settings().bot_tape_budget_seconds
    for a in watched:
        orders += _tape_asset(db, a, crowd, rng, now, held, cash, max_orders=3)
    served = 0
    for a in turn:
        if time.monotonic() > deadline:
            break
        orders += _tape_asset(db, a, crowd, rng, now, held, cash, max_orders=2)
        served += 1
    _tape_cursor = (_tape_cursor - k + served) % max(1, len(others))
    return {"orders": orders, "assets": len(watched) + served}


def run_round(db: Session, now: datetime | None = None, rng: random.Random | None = None,
              commit_each: bool = False) -> dict:
    """One market round: bots act on a sample of assets, then quotes are refreshed where
    the price moved. With commit_each the work is committed per asset so player orders
    never wait long behind a round; otherwise the caller commits."""
    s = get_settings()
    now = now or utcnow()
    if not market_hours.is_open(now):
        return {"assets": 0, "orders": 0, "closed": True}
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
    for asset in [a for a in assets if a.vi_until is None][:8]:  # a stock in VI waits for its auction
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
            # Conviction (봇 리그): bots that have been making money size up, losers size down.
            if _order(db, bot, asset, decision[0], decision[1] * bot_league.conviction(bot.username), rng, p.kind):
                orders += 1
                touched.add(asset.id)
                if commit_each:
                    db.commit()  # release the market maker's and the asset's row locks per order
                db.refresh(asset)
        if commit_each:
            db.commit()
    for asset in assets:
        _maybe_requote(db, asset, now, asset.id in touched, commit_each)
    league = bot_league.evaluate(db, bots, now)
    bot_league.save(db)
    if commit_each:
        db.commit()
    return {"assets": len(assets), "orders": orders, "touched": len(touched), "league": league}


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
