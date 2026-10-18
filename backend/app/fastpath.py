"""Speed-ups for a small single-process deployment far from its database.

Every database statement is a network round trip (100ms+ when the app and Postgres are in
different regions), so the hot paths avoid the database where it is safe to:

- the per-IP API rate limit is counted in memory (one process serves all requests);
- public market data that every viewer polls is shared for a second or two instead of
  being recomputed per viewer.
"""

import threading
import time

_lock = threading.Lock()
_windows: dict[str, tuple[int, int]] = {}


def memory_rate_limit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Fixed-window counter. Returns (allowed, seconds until the window resets)."""
    now = int(time.time())
    start = now - now % window_seconds
    with _lock:
        w, n = _windows.get(key, (start, 0))
        if w != start:
            w, n = start, 0
        n += 1
        _windows[key] = (w, n)
        if len(_windows) > 50_000:  # drop old windows
            for k in [k for k, (ws, _) in _windows.items() if ws != start]:
                del _windows[k]
    return n <= limit, window_seconds - now % window_seconds


# Public, user-independent GET endpoints → how long one response may be shared (seconds).
_SHARED = (
    ("/api/assets/", {"live": 1.0, "orderbook": 1.0, "trades": 1.0, "history": 2.0, "volume-profile": 3.0,
                     "growth": 30.0, "flows": 10.0}),
    ("/api/assets", 2.0),
    ("/api/rankings", 3.0),
    ("/api/market/summary", 3.0),
    ("/api/market/hours", 10.0),
    ("/api/leaderboard", 10.0),
    ("/api/announcements", 15.0),
)
_cache: dict[str, tuple[float, int, bytes, str]] = {}


def shared_ttl(path: str) -> float | None:
    for prefix, rule in _SHARED:
        if isinstance(rule, dict):
            if path.startswith(prefix):
                tail = path[len(prefix):].split("/")
                if len(tail) == 1:
                    return 2.0  # /api/assets/{id}
                return rule.get(tail[1])
        elif path == prefix:
            return rule
    return None


def cache_get(key: str) -> tuple[int, bytes, str] | None:
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1], hit[2], hit[3]
    return None


def cache_get_stale(key: str, ttl: float) -> tuple[int, bytes, str] | None:
    """An expired entry that is still recent enough to show while a fresh one is computed
    (stale-while-revalidate): viewers never wait on the database for public market data."""
    hit = _cache.get(key)
    grace = min(30.0, max(5.0, ttl * 10))
    if hit and hit[0] + grace > time.monotonic():
        return hit[1], hit[2], hit[3]
    return None


# Background refreshes in flight (one per key), and the secret that marks them as ours.
refreshing: set[str] = set()
REFRESH_TOKEN = __import__("secrets").token_hex(16)


def cache_put(key: str, ttl: float, status: int, body: bytes, media_type: str) -> None:
    if len(_cache) > 2_000:
        now = time.monotonic()
        for k in [k for k, v in _cache.items() if v[0] <= now]:
            _cache.pop(k, None)
    _cache[key] = (time.monotonic() + ttl, status, body, media_type)


def clear_responses() -> None:
    """Forget shared responses (after an operator changes what the market shows)."""
    _cache.clear()


def clear() -> None:
    _cache.clear()
    _windows.clear()



# Assets someone is looking at (asset pages poll their live panel): the price heartbeat
# ticks these first.
_views: dict[int, float] = {}


def mark_view(path: str) -> None:
    parts = path.split("/")  # ["", "api", "assets", "{id}", ...]
    if len(parts) >= 4 and parts[2] == "assets" and parts[3].isdigit():
        _views[int(parts[3])] = time.monotonic()


def watched_assets(within_seconds: float = 60.0) -> list[int]:
    cutoff = time.monotonic() - within_seconds
    for k in [k for k, t in _views.items() if t < cutoff]:
        _views.pop(k, None)
    return sorted(_views, key=_views.get, reverse=True)


# Player orders in flight. Bots wait for them before taking the market maker's locks, so a
# person's order never queues behind a bot's.
_orders_in_flight = 0


def order_started() -> None:
    global _orders_in_flight
    with _lock:
        _orders_in_flight += 1


def order_finished() -> None:
    global _orders_in_flight
    with _lock:
        _orders_in_flight = max(0, _orders_in_flight - 1)


def wait_for_players(max_seconds: float = 3.0) -> None:
    deadline = time.monotonic() + max_seconds
    while _orders_in_flight and time.monotonic() < deadline:
        time.sleep(0.05)
