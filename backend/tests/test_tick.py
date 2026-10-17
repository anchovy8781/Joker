"""POST /api/internal/tick (called by Supabase pg_cron every minute)."""

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.models import Job, PriceUpdate

SECRET = "t" * 40


def test_tick_disabled_or_wrong_secret_looks_like_404(app, monkeypatch):
    c = TestClient(app)
    monkeypatch.setattr(get_settings(), "tick_secret", "")
    assert c.post("/api/internal/tick", headers={"X-Tick-Secret": ""}).status_code == 404
    monkeypatch.setattr(get_settings(), "tick_secret", SECRET)
    assert c.post("/api/internal/tick").status_code == 404
    assert c.post("/api/internal/tick", headers={"X-Tick-Secret": "wrong" * 8}).status_code == 404


def test_tick_runs_scheduled_jobs_once(app, asset, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "tick_secret", SECRET)
    c = TestClient(app)
    r = c.post("/api/internal/tick", headers={"X-Tick-Secret": SECRET})
    assert r.status_code == 200
    ran = {j["type"]: j["status"] for j in r.json()["ran"]}
    assert ran.get("collect_data") == "succeeded" and ran.get("price_update") == "succeeded"
    assert db.execute(select(func.count(PriceUpdate.id))).scalar_one() == 1
    # Same minute again: dedup keys mean nothing is re-run and prices are not re-applied.
    again = c.post("/api/internal/tick", headers={"X-Tick-Secret": SECRET}).json()
    assert again["ran"] == []
    assert db.execute(select(func.count(PriceUpdate.id))).scalar_one() == 1
    assert db.execute(select(func.count(Job.id)).where(Job.status == "failed")).scalar_one() == 0


def test_production_requires_long_tick_secret(monkeypatch):
    import pytest

    from app.main import create_app

    s = get_settings()
    monkeypatch.setattr(s, "env", "production")
    monkeypatch.setattr(s, "ip_hash_secret", "x" * 48)
    monkeypatch.setattr(s, "cookie_secure", True)
    monkeypatch.setattr(s, "cors_origins", [])
    monkeypatch.setattr(s, "database_url", "postgresql+psycopg://u:strongpw@db/x")
    monkeypatch.setattr(s, "tick_secret", "short")
    with pytest.raises(RuntimeError, match="YEX_TICK_SECRET"):
        create_app()


def test_bootstrap_admin_is_idempotent(db, capsys):
    from app.cli import bootstrap
    from app.models import User

    env = {"YEX_BOOTSTRAP_ADMIN_USERNAME": "boss", "YEX_BOOTSTRAP_ADMIN_PASSWORD": "boss-password-1"}
    bootstrap(env)
    bootstrap({**env, "YEX_BOOTSTRAP_ADMIN_PASSWORD": "another-password-2"})  # second start: no change
    admins = db.execute(select(User).where(User.role == "admin")).scalars().all()
    assert [a.username for a in admins] == ["boss"]
    assert "created" in capsys.readouterr().out
    bootstrap({**env, "YEX_SEED_DEMO": "true"})
    from app.models import MarketAsset

    assert db.execute(select(func.count(MarketAsset.id))).scalar_one() == 8


def test_ping_identifies_the_app_cross_origin(app):
    r = TestClient(app).get("/api/ping", headers={"Origin": "https://localhost"})
    assert r.status_code == 200 and r.json() == {"app": "youtube-exchange"}
    assert r.headers["access-control-allow-origin"] == "*"
