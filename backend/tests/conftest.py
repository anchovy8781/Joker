import os
import uuid

os.environ.setdefault(
    "YEX_DATABASE_URL",
    os.environ.get("YEX_TEST_DATABASE_URL", "postgresql+psycopg://yex:yex_dev_password@localhost:5432/yex_test"),
)
os.environ["YEX_ENV"] = "test"
os.environ["YEX_COOKIE_SECURE"] = "false"
os.environ["YEX_YOUTUBE_API_KEY"] = ""
os.environ["YEX_RL_REGISTER_PER_IP"] = "[1000, 3600]"
os.environ["YEX_RL_LOGIN_PER_IP"] = "[1000, 300]"
os.environ["YEX_RL_ORDERS_PER_USER"] = "[10000, 60]"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from app.config import get_settings
from app.db import reset_engine, session_factory
from app.models import Order, User
from app.services.listing import approve_listing, get_market_maker, register_channel

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLES = [
    "user_missions", "notifications", "chat_messages", "trades", "ledger_entries", "audit_logs", "orders", "positions", "accounts", "sessions",
    "market_bars", "channel_metrics", "market_events", "price_updates", "risk_events", "appeals",
    "reports", "announcements", "jobs", "rate_limits", "reconciliation_runs", "api_quota_usage",
    "market_assets", "channels", "users",
]


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    url = get_settings().database_url
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    eng.dispose()
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_tables(migrated_db):
    reset_engine()
    eng = create_engine(get_settings().database_url)
    with eng.begin() as conn:
        # TRUNCATE is not covered by the row-level append-only triggers; tests only.
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    eng.dispose()
    yield
    reset_engine()


@pytest.fixture
def db():
    s = session_factory()()
    yield s
    s.close()


@pytest.fixture
def app():
    from app.main import create_app

    return create_app()


class ApiUser:
    """A browser-like client: own cookie jar, sends CSRF + client headers like the frontend."""

    def __init__(self, app, username: str | None = None, password: str = "correct-horse-battery"):
        self.client = TestClient(app)
        self.username = username or f"u_{uuid.uuid4().hex[:10]}"
        self.password = password
        self.id: int | None = None

    def headers(self) -> dict:
        h = {"X-Requested-With": "yex"}
        csrf = self.client.cookies.get("yex_csrf")
        if csrf:
            h["X-CSRF-Token"] = csrf
        return h

    def register(self):
        r = self.client.post("/api/auth/register", json={"username": self.username, "password": self.password},
                             headers=self.headers())
        assert r.status_code == 201, r.text
        self.id = r.json()["id"]
        return self

    def get(self, path, **kw):
        return self.client.get(path, **kw)

    def post(self, path, json=None, **kw):
        return self.client.post(path, json=json, headers={**self.headers(), **kw.pop("headers", {})}, **kw)

    def order(self, asset_id, side, qty, price=None, order_type=None, key=None):
        body = {"asset_id": asset_id, "side": side, "quantity": qty,
                "order_type": order_type or ("limit" if price is not None else "market"),
                "idempotency_key": key or uuid.uuid4().hex}
        if price is not None:
            body["limit_price"] = price
        return self.post("/api/orders", json=body)


@pytest.fixture
def make_user(app):
    def _make(**kw) -> ApiUser:
        return ApiUser(app, **kw).register()
    return _make


@pytest.fixture
def make_admin(app, db):
    from app.models import Account
    from app.security import hash_password

    def _make() -> ApiUser:
        u = ApiUser(app)
        user = User(username=u.username, normalized_username=u.username.lower(),
                    password_hash=hash_password(u.password), role="admin", verification_status="verified")
        db.add(user)
        db.flush()
        db.add(Account(user_id=user.id, cash_balance=0, reserved_cash=0))
        db.commit()
        r = u.client.post("/api/auth/login", json={"username": u.username, "password": u.password},
                          headers={"X-Requested-With": "yex"})
        assert r.status_code == 200, r.text
        u.id = user.id
        return u
    return _make


def admin_id(db) -> int:
    """An administrator for listing decisions made directly through the service layer."""
    from app.models import Account

    u = db.execute(select(User).where(User.normalized_username == "fixture_admin")).scalar_one_or_none()
    if u is None:
        u = User(username="fixture_admin", normalized_username="fixture_admin", role="admin",
                 password_hash=None, verification_status="verified")
        db.add(u)
        db.flush()
        db.add(Account(user_id=u.id, cash_balance=0, reserved_cash=0))
        db.flush()
    return u.id


def list_demo_asset(db, symbol="DTEST", price=10_000, shares=1_000_000, with_mm_quotes=True,
                    celebrity=False) -> int:
    ch = register_channel(db, youtube_channel_id=f"DEMO_{symbol.lower()}", title=f"[DEMO] {symbol}",
                          data_source="demo", category=None, actor_id=None)
    asset = approve_listing(db, channel_id=ch.id, symbol=symbol, initial_price=price,
                            shares_outstanding=shares, actor_id=admin_id(db), celebrity_new_channel=celebrity)
    if not with_mm_quotes:
        from app.services.orders import cancel_all_for_asset

        cancel_all_for_asset(db, asset.id, get_market_maker(db).id, "TEST_CLEAR")
    db.commit()
    return asset.id


@pytest.fixture
def asset(db):
    """A listed demo asset with the market maker's quotes on the book."""
    return list_demo_asset(db)


@pytest.fixture
def bare_asset(db):
    """A listed demo asset with an empty order book (market maker quotes removed)."""
    return list_demo_asset(db, with_mm_quotes=False)


def give_shares(db, user_id: int, asset_id: int, qty: int):
    """Move shares from the market maker to a user through a real trade on the book."""
    from app.services.orders import OrderRequest, place_order

    mm = get_market_maker(db)
    user = db.get(User, user_id)
    price = db.execute(text("SELECT reference_price FROM market_assets WHERE id=:a"), {"a": asset_id}).scalar_one()
    place_order(db, mm, OrderRequest(asset_id, "sell", "limit", qty, price, f"mmgive:{uuid.uuid4().hex}"))
    r = place_order(db, user, OrderRequest(asset_id, "buy", "limit", qty, price, f"give:{uuid.uuid4().hex}"))
    assert r.order.status == "filled", r.order.status
    db.commit()


def open_orders(db, asset_id: int):
    return db.execute(select(Order).where(Order.asset_id == asset_id,
                                          Order.status.in_(("open", "partially_filled")))).scalars().all()
