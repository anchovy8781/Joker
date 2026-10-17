"""SQLAlchemy ORM models.

All money values are integer credits (BIGINT). Average cost and realized PnL use NUMERIC.
Integrity rules are enforced by database constraints, not only by application code.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[int]:
    return mapped_column(BigInteger, primary_key=True, autoincrement=True)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user','admin','system')", name="ck_users_role"),
        CheckConstraint("status IN ('active','restricted','suspended')", name="ck_users_status"),
        CheckConstraint("risk_level IN ('low','medium','high')", name="ck_users_risk_level"),
        CheckConstraint(
            "verification_status IN ('unverified','verified','needs_verification')",
            name="ck_users_verification",
        ),
    )

    id: Mapped[int] = _pk()
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_username: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="user")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, server_default="low")
    verification_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="unverified"
    )
    signup_ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = _now()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = _pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("cash_balance >= 0", name="ck_accounts_cash_nonneg"),
        CheckConstraint("reserved_cash >= 0", name="ck_accounts_reserved_nonneg"),
        CheckConstraint("reserved_cash <= cash_balance", name="ck_accounts_reserved_le_cash"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    cash_balance: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    reserved_cash: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = _now()


class LedgerEntry(Base):
    """Immutable cash ledger. UPDATE/DELETE are blocked by a database trigger."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="ck_ledger_amount_nonzero"),
        CheckConstraint(
            "entry_type IN ('signup_grant','trade_buy','trade_sell','trade_fee',"
            "'admin_adjustment','correction')",
            name="ck_ledger_entry_type",
        ),
        Index(
            "uq_ledger_single_signup_grant",
            "user_id",
            unique=True,
            postgresql_where=text("entry_type = 'signup_grant'"),
        ),
        Index("ix_ledger_user_created", "user_id", "created_at"),
        Index("ix_ledger_reference", "reference_type", "reference_id"),
    )

    id: Mapped[int] = _pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default="CRD")
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(32))
    reference_id: Mapped[str | None] = mapped_column(String(64))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    memo: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = _now()


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (
        CheckConstraint(
            "listing_status IN ('pending','listed','rejected','delisted')", name="ck_channels_listing"
        ),
        CheckConstraint("data_source IN ('youtube_api','demo')", name="ck_channels_source"),
        CheckConstraint(
            "data_quality_status IN ('unknown','ok','stale','low_confidence','unavailable')",
            name="ck_channels_quality",
        ),
    )

    id: Mapped[int] = _pk()
    youtube_channel_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    handle: Mapped[str | None] = mapped_column(String(100))
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(64))
    data_source: Mapped[str] = mapped_column(String(16), nullable=False)
    listing_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    listed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_data_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_quality_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="unknown"
    )
    hidden_subscriber_count: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = _now()


class MarketAsset(Base):
    __tablename__ = "market_assets"
    __table_args__ = (
        CheckConstraint("reference_price >= 1", name="ck_assets_ref_price"),
        CheckConstraint("previous_price >= 1", name="ck_assets_prev_price"),
        CheckConstraint("current_price >= 1", name="ck_assets_cur_price"),
        CheckConstraint("shares_outstanding > 0", name="ck_assets_shares"),
        CheckConstraint("trading_status IN ('active','halted')", name="ck_assets_trading_status"),
    )

    id: Mapped[int] = _pk()
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False, unique=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    reference_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shares_outstanding: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trading_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    halt_reason: Mapped[str | None] = mapped_column(String(200))
    price_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_trade_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    created_at: Mapped[datetime] = _now()


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_positions_qty_nonneg"),
        CheckConstraint("reserved_quantity >= 0", name="ck_positions_reserved_nonneg"),
        CheckConstraint("reserved_quantity <= quantity", name="ck_positions_reserved_le_qty"),
        CheckConstraint("average_cost >= 0", name="ck_positions_avg_cost"),
        Index("ix_positions_asset", "asset_id"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("market_assets.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    reserved_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    average_cost: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False, server_default="0")
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False, server_default="0")
    updated_at: Mapped[datetime] = _now()


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("side IN ('buy','sell')", name="ck_orders_side"),
        CheckConstraint("order_type IN ('market','limit')", name="ck_orders_type"),
        CheckConstraint("quantity > 0", name="ck_orders_qty_pos"),
        CheckConstraint("remaining_quantity >= 0", name="ck_orders_remaining_nonneg"),
        CheckConstraint("remaining_quantity <= quantity", name="ck_orders_remaining_le_qty"),
        CheckConstraint("limit_price IS NULL OR limit_price > 0", name="ck_orders_limit_pos"),
        CheckConstraint(
            "(order_type = 'limit' AND limit_price IS NOT NULL) OR order_type = 'market'",
            name="ck_orders_limit_required",
        ),
        CheckConstraint("reserved_cash >= 0", name="ck_orders_reserved_cash"),
        CheckConstraint(
            "status IN ('open','partially_filled','filled','canceled','rejected')",
            name="ck_orders_status",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_orders_user_idempotency"),
        Index("ix_orders_book", "asset_id", "side", "status", "limit_price", "id"),
        Index("ix_orders_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = _pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    asset_id: Mapped[int] = mapped_column(ForeignKey("market_assets.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remaining_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    limit_price: Mapped[int | None] = mapped_column(BigInteger)
    reserved_cash: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Trade(Base):
    """Immutable execution record. UPDATE/DELETE are blocked by a database trigger."""

    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_trades_qty"),
        CheckConstraint("execution_price > 0", name="ck_trades_price"),
        CheckConstraint("buyer_fee >= 0 AND seller_fee >= 0", name="ck_trades_fees"),
        CheckConstraint("buyer_id <> seller_id", name="ck_trades_no_self_trade"),
        CheckConstraint("taker_side IN ('buy','sell')", name="ck_trades_taker_side"),
        Index("ix_trades_asset_executed", "asset_id", "executed_at"),
        Index("ix_trades_buyer", "buyer_id", "executed_at"),
        Index("ix_trades_seller", "seller_id", "executed_at"),
    )

    id: Mapped[int] = _pk()
    trade_uid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("market_assets.id"), nullable=False)
    buy_order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    sell_order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    execution_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    buyer_fee: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seller_fee: Mapped[int] = mapped_column(BigInteger, nullable=False)
    taker_side: Mapped[str] = mapped_column(String(4), nullable=False)
    seller_realized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    executed_at: Mapped[datetime] = _now()


class MarketBar(Base):
    __tablename__ = "market_bars"
    __table_args__ = (
        CheckConstraint("interval IN ('1h','1d')", name="ck_bars_interval"),
        CheckConstraint("low_price <= high_price", name="ck_bars_low_high"),
        CheckConstraint("volume >= 0", name="ck_bars_volume"),
    )

    asset_id: Mapped[int] = mapped_column(ForeignKey("market_assets.id"), primary_key=True)
    interval: Mapped[str] = mapped_column(String(4), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    high_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    low_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    close_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class ChannelMetric(Base):
    __tablename__ = "channel_metrics"
    __table_args__ = (
        CheckConstraint(
            "metric_type IN ('subscriber_count','view_count','video_count')", name="ck_metrics_type"
        ),
        CheckConstraint("metric_value >= 0", name="ck_metrics_value"),
        CheckConstraint("source IN ('youtube_api','demo')", name="ck_metrics_source"),
        UniqueConstraint(
            "channel_id", "metric_type", "source", "observed_date", name="uq_metrics_daily"
        ),
        Index("ix_metrics_channel_type_obs", "channel_id", "metric_type", "observed_at"),
    )

    id: Mapped[int] = _pk()
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_quality_status: Mapped[str] = mapped_column(String(24), nullable=False)


class MarketEvent(Base):
    __tablename__ = "market_events"
    __table_args__ = (
        CheckConstraint("event_score >= -1 AND event_score <= 1", name="ck_events_score"),
        CheckConstraint("expires_at > observed_at", name="ck_events_expiry"),
        Index("ix_events_asset_expiry", "asset_id", "expires_at"),
    )

    id: Mapped[int] = _pk()
    asset_id: Mapped[int] = mapped_column(ForeignKey("market_assets.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_score: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class PriceUpdate(Base):
    __tablename__ = "price_updates"
    __table_args__ = (
        UniqueConstraint("asset_id", "effective_date", name="uq_price_updates_asset_date"),
        CheckConstraint("status IN ('applied','skipped','failed')", name="ck_price_updates_status"),
    )

    id: Mapped[int] = _pk()
    asset_id: Mapped[int] = mapped_column(ForeignKey("market_assets.id"), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    old_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    new_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    formula_version: Mapped[str] = mapped_column(String(32), nullable=False)
    fundamental_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    flow_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    sentiment_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    raw_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    daily_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    flow_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    flow_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inputs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class RiskEvent(Base):
    __tablename__ = "risk_events"
    __table_args__ = (
        CheckConstraint("severity IN ('low','medium','high','critical')", name="ck_risk_severity"),
        Index("ix_risk_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = _pk()
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("market_assets.id"))
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    dedup_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = _now()
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolution: Mapped[str | None] = mapped_column(String(500))


class AuditLog(Base):
    """Append-only audit trail. UPDATE/DELETE are blocked by a database trigger."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_target", "target_type", "target_id"),
        Index("ix_audit_created", "created_at"),
    )

    id: Mapped[int] = _pk()
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _now()


class Job(Base):
    """PostgreSQL-backed job queue (claimed with FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed')", name="ck_jobs_status"
        ),
        Index("ix_jobs_claim", "status", "run_at"),
    )

    id: Mapped[int] = _pk()
    job_type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    dedup_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    run_at: Mapped[datetime] = _now()
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RateLimitCounter(Base):
    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (CheckConstraint("status IN ('ok','mismatch')", name="ck_recon_status"),)

    id: Mapped[int] = _pk()
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    checked_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    mismatches_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    totals_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _now()


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = _pk()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _now()


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("status IN ('open','resolved','dismissed')", name="ck_reports_status"),
        CheckConstraint(
            "target_type IN ('user','asset','channel','other')", name="ck_reports_target_type"
        ),
    )

    id: Mapped[int] = _pk()
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")
    created_at: Mapped[datetime] = _now()
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(500))


class Appeal(Base):
    __tablename__ = "appeals"
    __table_args__ = (
        CheckConstraint("status IN ('open','accepted','rejected')", name="ck_appeals_status"),
    )

    id: Mapped[int] = _pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    risk_event_id: Mapped[int | None] = mapped_column(ForeignKey("risk_events.id"))
    message: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")
    created_at: Mapped[datetime] = _now()
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(500))


class ApiQuotaUsage(Base):
    __tablename__ = "api_quota_usage"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    units_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
