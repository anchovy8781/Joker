"""Required tests 14, 15."""

from app.models import Account
from tests.conftest import give_shares


def test_14_cannot_view_or_modify_other_users_orders(make_user, bare_asset, db):
    alice, mallory = make_user(), make_user()
    order = alice.order(bare_asset, "buy", 2, price=9_900).json()["order"]
    # Read another user's order -> 404 (indistinguishable from nonexistent)
    r = mallory.get(f"/api/orders/{order['id']}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "ORDER_NOT_FOUND"
    assert mallory.get("/api/orders/999999").status_code == 404
    # Cancel another user's order -> 404, and nothing changes
    assert mallory.post(f"/api/orders/{order['id']}/cancel").status_code == 404
    assert alice.get(f"/api/orders/{order['id']}").json()["status"] == "open"
    # Lists only ever contain the caller's own data
    assert mallory.get("/api/orders").json() == []
    assert mallory.get("/api/trades").json() == []
    assert all(e["entry_type"] == "signup_grant" for e in mallory.get("/api/account/ledger").json())
    assert mallory.get("/api/account").json()["user_id"] == mallory.id


def test_14b_account_endpoints_ignore_client_supplied_user_ids(make_user, db):
    alice, mallory = make_user(), make_user()
    r = mallory.get(f"/api/account?user_id={alice.id}")
    assert r.json()["user_id"] == mallory.id
    r = mallory.get(f"/api/account/ledger?user_id={alice.id}")
    assert all(True for _ in r.json())
    db.expire_all()
    assert db.get(Account, alice.id).cash_balance == 1_000_000


def test_14c_anonymous_requests_rejected(app, asset):
    from fastapi.testclient import TestClient

    c = TestClient(app)
    for path in ("/api/account", "/api/account/positions", "/api/account/ledger", "/api/orders", "/api/trades"):
        assert c.get(path).status_code == 401, path
    r = c.post("/api/orders", json={}, headers={"X-Requested-With": "yex"})
    assert r.status_code in (401, 422)
    # Public market data stays public
    assert c.get("/api/assets").status_code == 200
    assert c.get("/api/leaderboard").status_code == 200


ADMIN_CALLS = [
    ("GET", "/api/admin/risk-events", None),
    ("GET", "/api/admin/audit-logs", None),
    ("POST", "/api/admin/assets/{a}/halt", {"reason": "abuse attempt"}),
    ("POST", "/api/admin/assets/{a}/resume", {"reason": "abuse attempt"}),
    ("GET", "/api/admin/channels", None),
    ("POST", "/api/admin/channels", {"youtube_channel_id": "DEMO_x_abuse", "title": "x", "data_source": "demo"}),
    ("GET", "/api/admin/jobs", None),
    ("POST", "/api/admin/jobs/price_update/run", None),
    ("GET", "/api/admin/reconciliation", None),
    ("POST", "/api/admin/reconciliation/run", None),
    ("POST", "/api/admin/adjustments", {"user_id": 1, "amount": 1_000_000, "reason": "give me money please",
                                        "idempotency_key": "steal-0001"}),
    ("POST", "/api/admin/announcements", {"title": "x", "body": "y"}),
    ("GET", "/api/admin/reports", None),
    ("GET", "/api/admin/appeals", None),
    ("POST", "/api/admin/users/1/status", {"status": "active", "reason": "self unban"}),
]


def test_15_regular_user_cannot_call_admin_api(make_user, asset, db):
    u = make_user()
    for method, path, body in ADMIN_CALLS:
        path = path.format(a=asset)
        r = u.get(path) if method == "GET" else u.post(path, json=body)
        assert r.status_code == 403, (path, r.status_code)
        assert r.json()["error"]["code"] == "FORBIDDEN"
    db.expire_all()
    assert db.get(Account, u.id).cash_balance == 1_000_000


def test_15b_admin_can_halt_and_resume_with_audit(make_admin, make_user, asset, db):
    admin, u = make_admin(), make_user()
    r = admin.post(f"/api/admin/assets/{asset}/halt", json={"reason": "suspicious volume review"})
    assert r.status_code == 200 and r.json()["trading_status"] == "halted"
    assert u.order(asset, "buy", 1).status_code == 409
    r = admin.post(f"/api/admin/assets/{asset}/resume", json={"reason": "review complete"})
    assert r.status_code == 200 and r.json()["trading_status"] == "active"
    logs = admin.get("/api/admin/audit-logs?action=asset.").json()
    halt = next(l for l in logs if l["action"] == "asset.halt")
    assert halt["actor_id"] == admin.id and halt["metadata"]["reason"] == "suspicious volume review"
    assert halt["target_id"] == str(asset) and halt["request_id"]


def test_admin_listing_flow(make_admin, make_user):
    admin = make_admin()
    r = admin.post("/api/admin/channels", json={"youtube_channel_id": "DEMO_new_listing", "title": "[DEMO] New",
                                                 "data_source": "demo"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert admin.post("/api/admin/channels", json={"youtube_channel_id": "DEMO_new_listing", "title": "dup",
                                                    "data_source": "demo"}).status_code == 409
    r = admin.post(f"/api/admin/channels/{cid}/approve", json={"symbol": "DNEW", "initial_price": 4_000,
                                                                "shares_outstanding": 50_000, "reason": "meets criteria"})
    assert r.status_code == 201, r.text
    u = make_user()
    assets = u.get("/api/assets?q=new").json()
    assert [a["symbol"] for a in assets] == ["DNEW"] and assets[0]["is_demo"] is True
    assert u.order(r.json()["asset_id"], "buy", 3).json()["order"]["status"] == "filled"


def test_errors_do_not_leak_internals(make_user, monkeypatch):
    from app.api import market

    def explode(*a, **k):
        raise RuntimeError("secret connection string postgres://user:pw@host")

    monkeypatch.setattr(market, "activity_24h", explode)
    from fastapi.testclient import TestClient
    from app.main import create_app

    c = TestClient(create_app(), raise_server_exceptions=False)
    r = c.get("/api/assets")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in r.text and "Traceback" not in r.text


def test_security_headers(make_user):
    r = make_user().get("/api/assets")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_sql_injection_in_search_is_inert(make_user, asset):
    u = make_user()
    r = u.get("/api/assets", params={"q": "' OR 1=1; DROP TABLE users; --"})
    assert r.status_code == 200 and r.json() == []
    assert u.get("/api/assets").status_code == 200
    r = u.get("/api/assets", params={"q": "%"})
    assert r.json() == []  # wildcard is escaped, not a match-all
