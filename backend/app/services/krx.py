"""Korea Exchange-style market rules: 호가단위 (tick sizes) and 변동성완화장치 (VI).

호가단위 — a limit price must be a multiple of the tick for its price range (KRX table since
2023): under 2,000 → 1; under 5,000 → 5; under 20,000 → 10; under 50,000 → 50;
under 200,000 → 100; under 500,000 → 500; otherwise 1,000. 상한가/하한가 (the daily ±band)
are rounded inward onto the tick grid.

VI — when a trade moves the price too far, continuous trading on that stock stops for
vi_seconds and orders are collected for a 단일가 매매 (call auction):
  * 정적 VI: ±vi_static_bps (10%) from the static base — today's reference price, or the
    price the last VI auction set;
  * 동적 VI: ±vi_dynamic_bps (3%) from the price about a minute earlier (this minute's open).
When the auction ends, everything collected plus the resting book is crossed at one price:
the price that trades the most shares (ties: the smaller imbalance, then the price closest
to the last trade). Orders that are left over continue in normal trading.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import MarketAsset, MarketBar, Order
from app.services.fees import bps_down, bps_up

KST = ZoneInfo("Asia/Seoul")
TICKS = ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500))
VI_REASON = "VI_CALL_AUCTION"


def tick_size(price: int) -> int:
    for bound, tick in TICKS:
        if price < bound:
            return tick
    return 1_000


def floor_tick(price: int) -> int:
    t = tick_size(price)
    p = price // t * t
    return p if tick_size(p) == t else floor_tick(p)


def ceil_tick(price: int) -> int:
    t = tick_size(price)
    return -(-price // t) * t


def on_tick(price: int) -> bool:
    return price % tick_size(price) == 0


def step(price: int, n: int) -> int:
    """n ticks above (n > 0) or below (n < 0) an on-tick price."""
    for _ in range(abs(n)):
        price = price + tick_size(price) if n > 0 else max(1, price - tick_size(max(1, price - 1)))
    return price


def limits(reference: int, band_bps: int) -> tuple[int, int]:
    """(하한가, 상한가) for a reference price and a band, on the tick grid."""
    lo = ceil_tick(max(get_settings().min_price, bps_down(reference, band_bps)))
    return lo, max(lo, floor_tick(bps_up(reference, band_bps)))


# ---- VI ---------------------------------------------------------------------------------

def in_vi(asset: MarketAsset, now: datetime) -> bool:
    return asset.vi_until is not None and now < asset.vi_until


def vi_due(asset: MarketAsset, now: datetime) -> bool:
    """A VI whose auction period is over and has not been crossed yet."""
    return asset.vi_until is not None and now >= asset.vi_until


def _static_base(asset: MarketAsset, now: datetime) -> int:
    same_day = asset.vi_last_at is not None and asset.vi_last_at.astimezone(KST).date() == now.astimezone(KST).date()
    return asset.vi_base if same_day and asset.vi_base else asset.reference_price


def minute_open(db: Session, asset_id: int, now: datetime) -> int | None:
    return db.execute(select(MarketBar.open_price).where(
        MarketBar.asset_id == asset_id, MarketBar.interval == "1m",
        MarketBar.timestamp == now.replace(second=0, microsecond=0))).scalar()


def _eligible(asset: MarketAsset, now: datetime) -> bool:
    s = get_settings()
    if not s.vi_enabled or asset.vi_until is not None or asset.trading_status != "active":
        return False
    # just came out of a VI: let the auction price settle first
    return asset.vi_last_at is None or now - asset.vi_last_at >= timedelta(seconds=s.vi_seconds + 60)


def _kind(asset: MarketAsset, now: datetime, minute_ref: int | None) -> str | None:
    s = get_settings()
    if not _eligible(asset, now):
        return None
    price, base = asset.current_price, _static_base(asset, now)
    if base and abs(price - base) * 10_000 >= base * s.vi_static_bps:
        return "static"
    if minute_ref and abs(price - minute_ref) * 10_000 >= minute_ref * s.vi_dynamic_bps:
        return "dynamic"
    return None


def would_trigger(asset: MarketAsset, now: datetime, minute_ref: int | None) -> bool:
    """Cheap pre-check without a database call (the heartbeat uses its own price memory)."""
    return _kind(asset, now, minute_ref) is not None


def check_vi(db: Session, asset: MarketAsset, now: datetime, minute_ref: int | None = None) -> str | None:
    """After a trade on a locked asset: start a VI if the price moved too far."""
    if not _eligible(asset, now):
        return None
    kind = _kind(asset, now, minute_ref if minute_ref is not None else minute_open(db, asset.id, now))
    if kind is None:
        return None
    asset.vi_until = now + timedelta(seconds=get_settings().vi_seconds)
    asset.vi_kind = kind
    asset.vi_last_at = now
    db.flush()
    return kind


def _effective(o: Order) -> int:
    return o.limit_price if o.limit_price is not None else (o.protect_price or 0)


def clearing_price(buys: list[Order], sells: list[Order], last: int) -> tuple[int, int]:
    """(price, volume) of a single-price auction; volume 0 when nothing crosses."""
    candidates = sorted({o.limit_price for o in buys + sells if o.limit_price is not None} | {last})
    best = (0, 0, 0, 0)  # volume, -imbalance, -distance, price
    for p in candidates:
        demand = sum(o.remaining_quantity for o in buys if _effective(o) >= p)
        supply = sum(o.remaining_quantity for o in sells if _effective(o) <= p)
        v = min(demand, supply)
        key = (v, -abs(demand - supply), -abs(p - last), p)
        if v and key > best:
            best = key
    return best[3], best[0]


def end_vi(db: Session, asset: MarketAsset, now: datetime) -> dict:
    """Cross the call auction, then let the leftovers continue in normal trading."""
    from app.services.ledger import lock_accounts
    from app.services.orders import RESTING, _execute, _release_remaining, activate_scheduled

    s = get_settings()
    asset.vi_until, kind = None, asset.vi_kind
    asset.vi_kind = None
    db.flush()
    book = list(db.execute(select(Order).where(
        Order.asset_id == asset.id,
        (Order.status.in_(RESTING)) | ((Order.status == "scheduled") & (Order.status_reason == VI_REASON)))
        .order_by(Order.id).with_for_update().execution_options(populate_existing=True)).scalars())
    lo, hi = limits(asset.reference_price, s.limit_band_bps)
    buys = sorted([o for o in book if o.side == "buy"], key=lambda o: (-_effective(o), o.id))
    sells = sorted([o for o in book if o.side == "sell"], key=lambda o: (_effective(o), o.id))
    price, volume = clearing_price(buys, sells, asset.current_price)
    price = min(max(price, lo), hi) if volume else asset.current_price
    traded = 0
    if volume:
        buys = [o for o in buys if _effective(o) >= price]
        sells = [o for o in sells if _effective(o) <= price]
        accounts = lock_accounts(db, {o.user_id for o in buys + sells})
        for b in buys:
            for sl in sells:
                if traded >= volume or b.remaining_quantity == 0:
                    break
                if sl.remaining_quantity == 0 or sl.user_id == b.user_id:
                    continue  # self-trade prevention
                qty = min(b.remaining_quantity, sl.remaining_quantity, volume - traded)
                _execute(db, asset, b, sl, qty, price, "buy" if b.id > sl.id else "sell", accounts, now)
                traded += qty
        for o in buys + sells:
            if o.remaining_quantity == 0 and o.status != "filled":
                o.status, o.status_reason, o.updated_at = "filled", None, now
                _release_remaining(db, o, accounts[o.user_id])
            elif o.status in RESTING and o.remaining_quantity < o.quantity:
                o.status = "partially_filled"
    asset.vi_base = asset.current_price
    db.flush()
    # Leftover auction orders join the continuous book (and match if they still cross it).
    for oid in [o.id for o in book if o.status == "scheduled"]:
        activate_scheduled(db, oid)
    return {"kind": kind, "price": price, "volume": traded}


def settle_due(db: Session, now: datetime) -> int:
    """Cross every call auction whose time is up (runs with the market heartbeat)."""
    from app.services import market_hours
    from app.services.listing import requote_market_maker
    from app.services.orders import lock_asset

    if not market_hours.is_open(now):
        return 0
    ids = db.execute(select(MarketAsset.id).where(MarketAsset.vi_until <= now)).scalars().all()
    for aid in ids:
        asset = lock_asset(db, aid)
        if vi_due(asset, now):
            end_vi(db, asset, now)
            db.commit()
            requote_market_maker(db, aid)
            db.commit()
    return len(ids)
