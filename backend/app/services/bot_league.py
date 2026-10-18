"""봇 리그: virtual traders are scored on what they earn, and the market rewards the good ones.

Every bot_reward_interval_minutes (while the market is open) each bot's profit for the period
is measured: equity (cash + shares at the current price) minus everything the system ever
funded it (seed, refills, rewards), so money handed to a bot is never counted as profit.
Then:
  * the top three with a positive return get a credit bonus (3% / 2% / 1% of their capital,
    a system_funding entry like their seed, so reconciliation still balances);
  * conviction (a size and order-flow multiplier, 0.5-2.0) rises for the top third and falls
    for the bottom third: capital flows to managers who make money, as in a real market;
  * 기관/외국인 desks also learn which signal to trade on (trend, value gap, 세력 flow, or
    against the trend). Each view they take is graded when it ends — did the price move the
    way they bet? — and a gradient bandit makes the strategies that paid more likely next
    time. The weakest desks partly copy the best desk of their kind.
State lives in memory and is saved to app_settings (key "bot_league") every persona round.
"""

import math
import random
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Account, AppSetting, LedgerEntry, MarketAsset, Position, User

KEY = "bot_league"
STRATEGIES = ("trend", "value", "flow", "contrarian")
STRATEGY_LABEL = {"trend": "추세추종", "value": "가치(괴리)", "flow": "수급추종", "contrarian": "역추세"}
REWARD_SHARE = (0.03, 0.02, 0.01)  # of the bot's starting capital, for ranks 1-3
CONV_MIN, CONV_MAX = 0.5, 2.0
LEARNING_RATE = 0.15

_state: dict = {"bots": {}, "base": {}, "last_eval": None, "history": []}
_loaded = False


def _initial_prefs(kind: str) -> dict[str, float]:
    if kind == "institution":
        return {"trend": 0.0, "value": 0.6, "flow": 0.0, "contrarian": 0.2}
    if kind == "foreign":
        return {"trend": 0.6, "value": 0.0, "flow": 0.3, "contrarian": 0.0}
    return {s: 0.0 for s in STRATEGIES}


def bot(name: str, kind: str = "") -> dict:
    b = _state["bots"].get(name)
    if b is None:
        b = {"H": _initial_prefs(kind), "rbar": 0.0, "conv": 1.0, "last_ret": None, "rank": None,
             "rewards": 0, "wins": 0, "podiums": 0, "views": 0, "hits": 0}
        _state["bots"][name] = b
    return b


def load(db: Session) -> None:
    global _loaded
    if _loaded:
        return
    row = db.get(AppSetting, KEY)
    if row is not None and isinstance(row.value, dict):
        _state.update({k: row.value[k] for k in ("bots", "base", "last_eval", "history") if k in row.value})
    _loaded = True


def save(db: Session) -> None:
    db.execute(insert(AppSetting).values(key=KEY, value=_state)
               .on_conflict_do_update(index_elements=["key"], set_={"value": _state}))


def reset_for_tests() -> None:
    global _loaded
    _state.clear()
    _state.update({"bots": {}, "base": {}, "last_eval": None, "history": []})
    _loaded = False


def probabilities(name: str, kind: str = "") -> dict[str, float]:
    h = bot(name, kind)["H"]
    top = max(h.values())
    e = {s: math.exp(h.get(s, 0.0) - top) for s in STRATEGIES}
    z = sum(e.values())
    return {s: e[s] / z for s in STRATEGIES}


def pick(name: str, kind: str, rng: random.Random) -> str:
    p = probabilities(name, kind)
    return rng.choices(STRATEGIES, weights=[p[s] for s in STRATEGIES])[0]


def credit(name: str, kind: str, strategy: str, outcome_pct: float) -> None:
    """Grade one finished view: outcome = how far the price moved in the direction the desk
    bet, in percent. Gradient bandit: raise the chosen strategy's preference by how much
    better than usual it did, lower the others."""
    b = bot(name, kind)
    r = max(-3.0, min(3.0, outcome_pct))
    p = probabilities(name, kind)
    adv = r - b["rbar"]
    for s in STRATEGIES:
        step = LEARNING_RATE * adv * ((1 - p[s]) if s == strategy else -p[s])
        b["H"][s] = max(-4.0, min(4.0, b["H"].get(s, 0.0) + step))
    b["rbar"] = 0.9 * b["rbar"] + 0.1 * r
    b["views"] += 1
    b["hits"] += 1 if r > 0 else 0


def conviction(name: str) -> float:
    b = _state["bots"].get(name)
    return b["conv"] if b else 1.0


def _books(db: Session, ids: list[int]) -> tuple[dict[int, int], dict[int, int]]:
    """(equity, everything the system ever funded) per bot."""
    equity = {a.user_id: a.cash_balance for a in db.execute(select(Account).where(Account.user_id.in_(ids))).scalars()}
    for uid, value in db.execute(select(Position.user_id, func.sum(Position.quantity * MarketAsset.current_price))
                                 .join(MarketAsset, MarketAsset.id == Position.asset_id)
                                 .where(Position.user_id.in_(ids)).group_by(Position.user_id)):
        equity[uid] = equity.get(uid, 0) + int(value or 0)
    funded = dict(db.execute(select(LedgerEntry.user_id, func.sum(LedgerEntry.amount)).where(
        LedgerEntry.user_id.in_(ids), LedgerEntry.entry_type == "system_funding").group_by(LedgerEntry.user_id)).all())
    return equity, {k: int(v) for k, v in funded.items()}


def evaluate(db: Session, users: list[User], now: datetime, force: bool = False) -> dict | None:
    """Close the scoring period if it is due: rank, reward, adjust conviction, imitate."""
    from app.services.bots import BY_NAME
    from app.services.ledger import lock_account, post_entry

    load(db)
    last = _state.get("last_eval")
    due = force or last is None or now - datetime.fromisoformat(last) >= timedelta(
        minutes=get_settings().bot_reward_interval_minutes)
    if not due or not users:
        return None
    equity, funded = _books(db, [u.id for u in users])
    base = _state["base"]
    period = []
    for u in users:
        p = BY_NAME[u.username]
        pnl_now = equity.get(u.id, 0) - funded.get(u.id, 0)  # lifetime profit
        prev = base.get(u.username)
        base[u.username] = [equity.get(u.id, 0), funded.get(u.id, 0)]
        if prev is None:
            continue
        pnl = pnl_now - (prev[0] - prev[1])
        period.append((pnl / max(prev[0], p.capital // 2, 1), u, p))
    _state["last_eval"] = now.isoformat()
    if not period:
        return {"ranked": 0}
    period.sort(key=lambda t: t[0], reverse=True)
    n = len(period)
    third = max(1, n // 3)
    top = []
    for i, (ret, u, p) in enumerate(period):
        b = bot(u.username, p.kind)
        b["last_ret"], b["rank"] = round(ret, 6), i + 1
        conv = 1 + (b["conv"] - 1) * 0.95  # slowly forget old form
        if i < third and ret > 0:
            conv *= 1.12
        elif i >= n - third and ret < 0:
            conv *= 0.9
        b["conv"] = round(max(CONV_MIN, min(CONV_MAX, conv)), 4)
        reward = 0
        if i < len(REWARD_SHARE) and ret > 0:
            reward = int(p.capital * REWARD_SHARE[i])
            post_entry(db, lock_account(db, u.id), entry_type="system_funding", amount=reward,
                       transaction_id=uuid.uuid4(), reference_type="bot_reward",
                       reference_id=f"{now.strftime('%Y-%m-%dT%H:%M')}:{u.id}",
                       memo=f"봇 리그 {i + 1}위 보상")
            base[u.username][1] += reward  # a reward is funding, not profit
            b["rewards"] += reward
            b["podiums"] += 1
            b["wins"] += 1 if i == 0 else 0
        if i < 3:
            top.append({"name": u.username, "ret": round(ret, 6), "reward": reward})
    # The weakest desks partly copy the best desk of their kind (imitation of what works).
    for kind in ("institution", "foreign"):
        desks = [(ret, u) for ret, u, p in period if p.kind == kind]
        if len(desks) >= 2 and desks[0][0] > desks[-1][0]:
            best, worst = bot(desks[0][1].username), bot(desks[-1][1].username)
            for s in STRATEGIES:
                worst["H"][s] = 0.7 * worst["H"].get(s, 0.0) + 0.3 * best["H"].get(s, 0.0)
    _state["history"] = ([{"at": now.isoformat(), "top": top}] + _state["history"])[:12]
    return {"ranked": n, "top": top}


def standings(db: Session) -> dict:
    """The public 봇 리그 table: lifetime and last-period returns, rewards and learned styles."""
    from app.services.bots import FLOW_GROUP, PERSONAS

    load(db)
    users = {u.username: u for u in db.execute(select(User).where(
        User.normalized_username.in_([p.username.lower() for p in PERSONAS]))).scalars()}
    equity, funded = _books(db, [u.id for u in users.values()]) if users else ({}, {})
    rows = []
    for p in PERSONAS:
        u = users.get(p.username)
        if u is None:
            continue
        b = _state["bots"].get(p.username) or {}
        f = funded.get(u.id, 0)
        row = {"name": p.username, "group": FLOW_GROUP.get(p.kind, "개인"), "kind": p.kind,
               "equity": equity.get(u.id, 0), "profit": equity.get(u.id, 0) - f,
               "return": round((equity.get(u.id, 0) - f) / f, 6) if f else 0.0,
               "last_return": b.get("last_ret"), "rank": b.get("rank"), "conviction": b.get("conv", 1.0),
               "rewards": b.get("rewards", 0), "wins": b.get("wins", 0), "podiums": b.get("podiums", 0)}
        if p.kind in ("institution", "foreign"):
            probs = probabilities(p.username, p.kind)
            best = max(probs, key=probs.get)
            row["strategy"] = {"best": best, "label": STRATEGY_LABEL[best],
                               "mix": {STRATEGY_LABEL[s]: round(v, 3) for s, v in probs.items()},
                               "hit_rate": round(b["hits"] / b["views"], 3) if b.get("views") else None}
        rows.append(row)
    rows.sort(key=lambda r: r["return"], reverse=True)
    return {"bots": rows, "last_eval": _state.get("last_eval"), "history": _state.get("history", []),
            "interval_minutes": get_settings().bot_reward_interval_minutes,
            "strategies": STRATEGY_LABEL}
