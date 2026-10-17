"""Trading sessions in Korea time (KST).

    Mon, Thu        17:00 – 23:10
    Tue, Wed, Fri   16:00 – 23:10   (additional trading time from 16:00)
    Sat, Sun        13:00 – 22:00

Outside a session, buy/sell orders (market and limit) are accepted as reservations
(status 'scheduled'): cash/shares are reserved immediately and the orders enter the book
in submission order when the next session opens.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings

KST = ZoneInfo("Asia/Seoul")

# weekday() -> (open, close)
SESSIONS: dict[int, tuple[time, time]] = {
    0: (time(17, 0), time(23, 10)),  # Monday
    1: (time(16, 0), time(23, 10)),  # Tuesday (additional time)
    2: (time(16, 0), time(23, 10)),  # Wednesday (additional time)
    3: (time(17, 0), time(23, 10)),  # Thursday
    4: (time(16, 0), time(23, 10)),  # Friday (additional time)
    5: (time(13, 0), time(22, 0)),   # Saturday
    6: (time(13, 0), time(22, 0)),   # Sunday
}
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


@dataclass
class MarketStatus:
    open: bool
    session_open: datetime  # today's (KST) session
    session_close: datetime
    next_open: datetime
    next_close: datetime
    enforced: bool


def _session(d: date) -> tuple[datetime, datetime]:
    o, c = SESSIONS[d.weekday()]
    return datetime.combine(d, o, KST), datetime.combine(d, c, KST)


def status(now: datetime) -> MarketStatus:
    local = now.astimezone(KST)
    s_open, s_close = _session(local.date())
    enforced = get_settings().market_hours_enabled
    is_open = (not enforced) or (s_open <= local < s_close)
    if local < s_open:
        n_open, n_close = s_open, s_close
    else:
        n_open, n_close = _session(local.date() + timedelta(days=1))
    if is_open and enforced:
        n_open, n_close = s_open, s_close
    return MarketStatus(is_open, s_open, s_close, n_open, n_close, enforced)


def is_open(now: datetime) -> bool:
    return status(now).open


def schedule_table() -> list[dict]:
    return [{"weekday": WEEKDAY_KO[i], "open": o.strftime("%H:%M"), "close": c.strftime("%H:%M")}
            for i, (o, c) in SESSIONS.items()]
