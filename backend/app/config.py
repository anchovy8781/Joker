"""Application settings, loaded from environment variables (see .env.example)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YEX_", env_file=".env", extra="ignore")

    env: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://yex:yex_dev_password@localhost:5432/yex"

    # Security
    session_ttl_hours: int = 24 * 7
    cookie_secure: bool = True  # must stay True in production (HTTPS only)
    cookie_samesite: str = "lax"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    # Secret used to hash IP addresses before storage (never store raw IPs).
    ip_hash_secret: str = "change-me-in-env"
    trust_proxy_headers: bool = False

    # Optional free Cloudflare Turnstile CAPTCHA for high-risk signups. Empty = disabled.
    turnstile_secret: str = ""

    # Economy (all amounts are integer credits)
    signup_grant: int = 1_000_000
    fee_bps: int = 20  # 0.20%
    min_price: int = 100
    max_order_quantity: int = 100_000

    # Order engine
    market_order_max_deviation_bps: int = 500  # market orders may walk at most 5% from reference
    limit_band_bps: int = 1500  # limit prices must be within ±15% of reference
    low_liquidity_band_bps: int = 700  # stricter band for illiquid assets
    low_liquidity_daily_trades: int = 5

    # Market maker (IPO inventory provider). It only trades with its own inventory and cash.
    mm_spread_bps: list[int] = Field(default_factory=lambda: [50, 150, 300])
    mm_level_fraction_bps: int = 50  # each level offers 0.5% of remaining inventory

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

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
