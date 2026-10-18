"""Application settings, loaded from environment variables (see .env.example)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YEX_", env_file=".env", extra="ignore")

    env: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://yex:yex_dev_password@localhost:5432/yex"
    # Hosted Postgres (e.g. Supabase pooler) limits connections: keep the pool small there.
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # Secret for POST /api/internal/tick (called every minute by Supabase pg_cron in the
    # cloud deployment instead of a separate worker process). Empty = endpoint disabled.
    tick_secret: str = ""

    # Security
    session_ttl_hours: int = 24 * 7
    cookie_secure: bool = True  # must stay True in production (HTTPS only)
    cookie_samesite: str = "lax"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    # Secret used to hash IP addresses before storage (never store raw IPs).
    ip_hash_secret: str = "change-me-in-env"
    # Number of trusted reverse proxies that append to X-Forwarded-For (e.g. Caddy/nginx = 1).
    # 0 = ignore the header entirely. Next.js rewrites pass headers through without appending,
    # so they do not count as a hop. Never set this when the API is directly reachable.
    trusted_proxy_hops: int = 0

    # Optional free Cloudflare Turnstile CAPTCHA for high-risk signups. Empty = disabled.
    turnstile_secret: str = ""

    # Economy (all amounts are integer credits)
    signup_grant: int = 1_000_000
    admin_grant: int = 100_000_000  # one-time starting balance for administrators
    # Users pay this to request a listing; refunded in full if an administrator rejects it.
    listing_fee: int = 100_000
    listing_window_enabled: bool = True  # False: listing requests accepted around the clock
    fee_bps: int = 20  # 0.20%
    # Surcharge fee for hot assets (new video upload / newly listed celebrity channel).
    # Selling a surcharged asset at a loss pays no sell fee.
    surcharge_fee_bps: int = 100  # 1.00%
    new_video_surcharge_hours: int = 48
    celebrity_surcharge_days: int = 30
    min_price: int = 100
    max_order_quantity: int = 100_000

    # Order engine
    # Market orders may walk at most 5% from the last traded price (inside the daily band).
    market_order_max_deviation_bps: int = 500
    limit_band_bps: int = 3000  # daily price limit: ±30% of the reference price (KRX-style)
    low_liquidity_band_bps: int = 2000  # tighter band for assets that barely traded
    low_liquidity_daily_trades: int = 5

    # Market maker (IPO inventory provider). It only trades with its own inventory and cash.
    # Ladder of small quotes so that real volume moves the price.
    mm_levels: int = 6
    mm_half_spread_bps: int = 10  # finer ticks: price steps of ~0.1-0.15% instead of 0.2-0.3%
    mm_step_bps: int = 15
    mm_level_value: int = 300_000  # credits per level near the touch (deeper levels a bit larger)
    mm_follow_bps: int = 9000  # quote centre = 90% last trade + 10% intraday fair value
    mm_cash_per_asset: int = 30_000_000  # system-funded bid liquidity so selling always works
    intraday_max_adj_bps: int = 400  # quote centre may move at most ±4% off the daily reference
    price_alerts_max_active: int = 20  # each level offers 0.5% of remaining inventory

    # Live channel statistics (cheap channels.list call, 1 quota unit per 50 channels).
    live_stats_interval_minutes: int = 2  # YouTube stats sample (1 quota unit per 50 channels per run)
    live_samples_keep_days: int = 7

    # Trading sessions (Korea time). Disable only for tests/local experiments.
    auto_halt_enabled: bool = False  # data problems show a badge instead of halting trading
    market_hours_enabled: bool = False  # 24h trading; True restores sessions + reservations

    # Virtual traders (세력·개미) that keep the market moving.
    bots_enabled: bool = True
    bot_interval_seconds: float = 4.0  # price heartbeat (small retail orders) every ~3-5s
    bot_persona_interval_seconds: float = 600.0  # whales / value investors / momentum decide every 10 min
    bot_max_orders_per_round: int = 12
    bot_tape_rotation: int = 50  # unwatched assets traded per heartbeat tick, in turn (50 = all)
    bot_tape_budget_seconds: float = 2.5  # unwatched assets share this much time per tick; the rest go next tick
    bot_reward_interval_minutes: int = 60  # 봇 리그: score, reward and adapt the bots this often
    bot_round_budget_seconds: float = 4.0  # stop placing bot orders after this much wall time

    # Listing requests: open daily from this hour (KST) until midnight, global daily cap.
    listing_request_open_hour_kst: int = 19
    listing_requests_per_day: int = 25

    # Donation box (user-to-user virtual credit gifts)
    donation_box_daily_limit: int = 500_000  # max a user can receive (and send) per KST day
    donation_min_amount: int = 100
    donation_box_min_goal: int = 1_000
    donation_box_hours: int = 24  # a chat donation box stays open this long
    donation_min_account_age_hours: int = 72
    donation_min_trades: int = 5

    # Auto (price-triggered) orders
    auto_orders_max_active: int = 20
    auto_orders_max_days: int = 30

    # Data freshness
    stale_after_hours: int = 48

    # YouTube Data API v3 (free quota; no billing required). Empty key => demo provider only.
    youtube_api_key: str = ""
    youtube_daily_quota: int = 10_000
    youtube_trending_regions: list[str] = Field(default_factory=lambda: ["KR", "US"])

    # Rate limits: (count, window seconds)
    rl_register_per_ip: tuple[int, int] = (5, 3600)
    rl_login_per_ip: tuple[int, int] = (20, 300)
    rl_login_fail_per_user: tuple[int, int] = (8, 900)
    rl_orders_per_user: tuple[int, int] = (60, 60)
    rl_orders_per_user_medium_risk: tuple[int, int] = (10, 60)
    rl_api_per_ip: tuple[int, int] = (600, 60)
    rl_chat_per_user: tuple[int, int] = (10, 60)
    rl_listing_requests_per_user: tuple[int, int] = (3, 86400)

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
