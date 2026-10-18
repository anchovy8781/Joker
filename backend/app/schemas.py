"""Typed request/response models. Requests forbid unknown fields so clients cannot slip in
server-owned values such as prices, fees or balances."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- auth
class RegisterIn(Strict):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=10, max_length=128)
    captcha_token: str | None = Field(default=None, max_length=4096)


class LoginIn(Strict):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class UserOut(Out):
    id: int
    username: str
    role: str
    status: str
    risk_level: str
    verification_status: str
    created_at: datetime
    last_login_at: datetime | None


# ---------------------------------------------------------------- market
class ChannelOut(Out):
    id: int
    youtube_channel_id: str
    title: str
    handle: str | None
    thumbnail_url: str | None
    category: str | None
    data_source: Literal["youtube_api", "demo"]
    listing_status: str
    listed_at: datetime | None
    last_data_update_at: datetime | None
    data_quality_status: str
    hidden_subscriber_count: bool


class FreshnessOut(BaseModel):
    status: Literal["fresh", "stale", "never"]
    last_update_at: datetime | None
    age_hours: float | None
    stale_after_hours: int


class AssetOut(BaseModel):
    id: int
    symbol: str
    channel: ChannelOut
    reference_price: int
    previous_price: int
    current_price: int
    change: int
    change_pct: float
    shares_outstanding: int
    trading_status: str
    halt_reason: str | None
    price_updated_at: datetime | None
    last_trade_at: datetime | None
    volume_24h: int
    trades_24h: int
    turnover_24h: int  # 거래대금 (sum of quantity × price, 24h)
    market_cap: int  # 시가총액 (current price × shares outstanding)
    sparkline: list[int] = []
    popularity_score: int | None  # 인기도 1..50
    popularity_updated_at: datetime | None
    fee_bps: int  # fee rate a new order would get right now
    fee_surcharge_reason: str | None  # 'new_video' | 'celebrity_new_channel'
    fee_surcharge_until: datetime | None
    freshness: FreshnessOut
    is_demo: bool


class MetricPoint(BaseModel):
    observed_date: str
    subscriber_count: int | None
    view_count: int | None
    video_count: int | None
    source: str


class BarOut(Out):
    timestamp: datetime
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    volume: int


class BookLevel(BaseModel):
    price: int
    quantity: int
    orders: int


class OrderBookOut(BaseModel):
    asset_id: int
    bids: list[BookLevel]
    asks: list[BookLevel]


class TradeOut(Out):
    id: int
    trade_uid: Any
    asset_id: int
    quantity: int
    execution_price: int
    taker_side: str
    executed_at: datetime


class MyTradeOut(TradeOut):
    side: Literal["buy", "sell"]
    fee: int
    fee_waived: bool = False
    realized_pnl: Decimal | None
    symbol: str


# ---------------------------------------------------------------- orders
class OrderIn(Strict):
    asset_id: int = Field(gt=0)
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    quantity: int = Field(gt=0)
    limit_price: int | None = Field(default=None, gt=0)
    idempotency_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_\-:.]+$")


class OrderOut(Out):
    id: int
    asset_id: int
    side: str
    order_type: str
    quantity: int
    remaining_quantity: int
    filled_quantity: int
    limit_price: int | None
    reserved_cash: int
    fee_bps: int
    status: str
    status_reason: str | None
    idempotency_key: str
    created_at: datetime
    updated_at: datetime


class PlaceOrderOut(BaseModel):
    order: OrderOut
    fills: list[TradeOut]
    replayed: bool


# ---------------------------------------------------------------- account
class AccountOut(BaseModel):
    user_id: int
    cash_balance: int
    reserved_cash: int
    available_cash: int
    positions_value: int
    total_equity: int
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    positions_count: int
    # progression (see services/progress.py)
    xp: int = 0
    level: int = 1
    level_start_xp: int = 0
    next_level_xp: int = 100
    tier: str = "브론즈"
    next_tier_equity: int | None = None
    currency: str = "CRD"
    note: str = "Virtual credits only. No cash value; cannot be bought, sold or exchanged."


class PositionOut(BaseModel):
    asset_id: int
    symbol: str
    title: str
    is_demo: bool
    quantity: int
    reserved_quantity: int
    average_cost: Decimal
    current_price: int
    market_value: int
    unrealized_pnl: Decimal
    realized_pnl: Decimal


class LedgerOut(Out):
    id: int
    transaction_id: Any
    entry_type: str
    amount: int
    balance_after: int
    currency: str
    reference_type: str | None
    reference_id: str | None
    memo: str | None
    created_at: datetime


class LeaderboardRow(BaseModel):
    rank: int
    username: str
    total_equity: int
    cash_balance: int
    positions_value: int
    return_pct: float
    tier: str = "브론즈"


# ---------------------------------------------------------------- user reports / appeals
class ReportIn(Strict):
    target_type: Literal["user", "asset", "channel", "chat_message", "other"]
    target_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(min_length=5, max_length=1000)


class AppealIn(Strict):
    risk_event_id: int | None = None
    message: str = Field(min_length=10, max_length=2000)


# ---------------------------------------------------------------- admin
class ChatIn(Strict):
    body: str = Field(min_length=1, max_length=1000)


class ListingRequestIn(Strict):
    channel: str = Field(min_length=3, max_length=200)
    note: str | None = Field(default=None, max_length=500)


class MarkReadIn(Strict):
    ids: list[int] | None = Field(default=None, max_length=500)
    all: bool = False


class AutoOrderIn(Strict):
    asset_id: int = Field(gt=0)
    side: Literal["buy", "sell"]
    trigger_condition: Literal["lte", "gte"]  # fire when price <= / >= trigger_price
    trigger_price: int = Field(gt=0)
    order_type: Literal["market", "limit"]
    limit_price: int | None = Field(default=None, gt=0)
    quantity: int = Field(gt=0, le=100_000)
    days: int = Field(default=7, ge=1, le=30)


class BoxOpenIn(Strict):
    title: str = Field(min_length=2, max_length=60)
    goal: int = Field(gt=0, le=500_000)


class BoxGiveIn(Strict):
    amount: int = Field(gt=0, le=500_000)
    message: str | None = Field(default=None, max_length=200)


class TipIn(Strict):
    to: str = Field(min_length=3, max_length=21)
    amount: int = Field(gt=0, le=500_000)
    message: str | None = Field(default=None, max_length=200)


class PriceAlertIn(Strict):
    asset_id: int = Field(gt=0)
    direction: Literal["above", "below"]
    target_price: int = Field(gt=0, le=1_000_000_000)


class DonationIn(Strict):
    recipient: str = Field(min_length=3, max_length=20)
    amount: int = Field(gt=0, le=500_000)
    message: str | None = Field(default=None, max_length=200)


class ReasonIn(Strict):
    reason: str = Field(default="관리자 원터치 처리", min_length=3, max_length=500)


class ChannelRegisterIn(Strict):
    # Channel ID, channel URL, @handle or bare handle (resolved server-side for youtube_api).
    youtube_channel_id: str = Field(min_length=3, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    data_source: Literal["youtube_api", "demo"]
    category: str | None = Field(default=None, max_length=64)


class ListingApproveIn(Strict):
    # Omitted terms are filled from the server's suggestion (see channel_lookup.suggest_listing).
    symbol: str | None = Field(default=None, min_length=2, max_length=12)
    initial_price: int | None = Field(default=None, ge=100, le=10_000_000)
    shares_outstanding: int | None = Field(default=None, ge=1_000, le=1_000_000_000)
    reason: str = Field(default="관리자 원터치 승인", min_length=3, max_length=500)
    celebrity_new_channel: bool = False


class SettingIn(Strict):
    value: bool | int | float


class QuickListIn(Strict):
    channel: str = Field(min_length=2, max_length=200)
    celebrity_new_channel: bool = False


class RiskReviewIn(Strict):
    decision: Literal["dismiss", "confirm"]
    resolution: str = Field(min_length=3, max_length=500)


class UserStatusIn(Strict):
    status: Literal["active", "restricted", "suspended"]
    reason: str = Field(default="관리자 원터치 처리", min_length=2, max_length=500)


class AdjustmentIn(Strict):
    user_id: int
    amount: int = Field(ge=-10_000_000, le=10_000_000, description="non-zero; validated by the ledger")
    reason: str = Field(min_length=10, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=64)


class AnnouncementIn(Strict):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)


class ResolveIn(Strict):
    status: Literal["resolved", "dismissed", "accepted", "rejected"]
    resolution: str = Field(min_length=3, max_length=500)


class PageMeta(BaseModel):
    limit: int
    offset: int
    total: int | None = None
