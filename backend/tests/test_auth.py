"""Required tests 1, 2 + authentication/session security."""

import threading

from sqlalchemy import func, select

from app.config import get_settings
from app.models import LedgerEntry, User
from app.services.auth import grant_signup_bonus
from tests.conftest import ApiUser


def _grants(db, user_id):
    return db.execute(select(func.count()).select_from(LedgerEntry).where(
        LedgerEntry.user_id == user_id, LedgerEntry.entry_type == "signup_grant")).scalar_one()


def test_01_signup_grant_paid_exactly_once(make_user, db):
    u = make_user()
    acct = u.get("/api/account").json()
    assert acct["cash_balance"] == get_settings().signup_grant == 1_000_000
    assert _grants(db, u.id) == 1
    ledger = u.get("/api/account/ledger").json()
    assert [e["entry_type"] for e in ledger] == ["signup_grant"]
    assert ledger[0]["amount"] == 1_000_000 and ledger[0]["balance_after"] == 1_000_000


def test_02_repeated_grant_calls_do_not_double_pay(make_user, db):
    u = make_user()
    # Sequential retries
    assert grant_signup_bonus(db, u.id) is False
    db.commit()
    # Concurrent retries from separate DB sessions
    from app.db import session_factory

    results = []

    def worker():
        s = session_factory()()
        try:
            results.append(grant_signup_bonus(s, u.id))
            s.commit()
        except Exception as exc:  # noqa: BLE001
            results.append(type(exc).__name__)
            s.rollback()
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(r is False for r in results), results
    db.expire_all()
    assert _grants(db, u.id) == 1
    assert u.get("/api/account").json()["cash_balance"] == 1_000_000


def test_02b_duplicate_registration_requests_create_one_account(app, db):
    name = "dup_user_x"
    codes = []

    def worker():
        c = ApiUser(app, username=name)
        r = c.client.post("/api/auth/register", json={"username": name, "password": "correct-horse-battery"},
                          headers={"X-Requested-With": "yex"})
        codes.append(r.status_code)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes).count(201) == 1, codes
    assert set(codes) <= {201, 409}
    users = db.execute(select(User).where(User.normalized_username == name)).scalars().all()
    assert len(users) == 1
    assert _grants(db, users[0].id) == 1
    # Case-insensitive uniqueness
    r = ApiUser(app, username="DUP_USER_X").client.post(
        "/api/auth/register", json={"username": "DUP_USER_X", "password": "correct-horse-battery"},
        headers={"X-Requested-With": "yex"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "USERNAME_TAKEN"


def test_password_hashed_with_argon2_and_login_flow(make_user, app, db):
    u = make_user()
    user = db.get(User, u.id)
    assert user.password_hash.startswith("$argon2id$") and u.password not in user.password_hash
    c = ApiUser(app, username=u.username)
    bad = c.client.post("/api/auth/login", json={"username": u.username, "password": "wrong-password!"},
                        headers={"X-Requested-With": "yex"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "INVALID_CREDENTIALS"
    ok = c.client.post("/api/auth/login", json={"username": u.username.upper(), "password": u.password},
                       headers={"X-Requested-With": "yex"})
    assert ok.status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["username"] == u.username
    out = c.post("/api/auth/logout")
    assert out.status_code == 204
    assert c.get("/api/account").status_code == 401


def test_session_cookie_flags(app):
    c = ApiUser(app)
    r = c.client.post("/api/auth/register", json={"username": c.username, "password": c.password},
                      headers={"X-Requested-With": "yex"})
    cookies = r.headers.get_list("set-cookie")
    session = next(h for h in cookies if h.startswith("yex_session="))
    csrf = next(h for h in cookies if h.startswith("yex_csrf="))
    assert "HttpOnly" in session and "SameSite=lax" in session
    assert "HttpOnly" not in csrf
    assert "password" not in r.text


def test_csrf_token_required_for_state_changes(make_user, asset):
    u = make_user()
    body = {"asset_id": asset, "side": "buy", "order_type": "market", "quantity": 1, "idempotency_key": "csrf-test-1"}
    r = u.client.post("/api/orders", json=body, headers={"X-Requested-With": "yex"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"
    r = u.client.post("/api/orders", json=body, headers={"X-Requested-With": "yex", "X-CSRF-Token": "forged"})
    assert r.status_code == 403
    r = u.client.post("/api/orders", json=body, headers={"X-CSRF-Token": u.client.cookies.get("yex_csrf")})
    assert r.status_code == 403  # missing custom client header
    assert u.post("/api/orders", json=body).status_code == 201


def test_login_requires_client_header(app):
    c = ApiUser(app)
    r = c.client.post("/api/auth/register", json={"username": c.username, "password": c.password})
    assert r.status_code == 403


def test_register_rate_limited_per_ip(app, monkeypatch):
    monkeypatch.setattr(get_settings(), "rl_register_per_ip", (2, 3600))
    codes = []
    for _ in range(3):
        c = ApiUser(app)
        codes.append(c.client.post("/api/auth/register", json={"username": c.username, "password": c.password},
                                   headers={"X-Requested-With": "yex"}).status_code)
    assert codes == [201, 201, 429]


def test_failed_logins_rate_limited_per_user(make_user, app, monkeypatch):
    monkeypatch.setattr(get_settings(), "rl_login_fail_per_user", (3, 900))
    u = make_user()
    c = ApiUser(app, username=u.username)
    codes = [c.client.post("/api/auth/login", json={"username": u.username, "password": "nope-nope-nope"},
                           headers={"X-Requested-With": "yex"}).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    # Even the right password is refused while locked out
    assert c.client.post("/api/auth/login", json={"username": u.username, "password": u.password},
                         headers={"X-Requested-With": "yex"}).status_code == 429


def test_many_signups_same_network_flagged_not_blocked(app, db):
    users = [ApiUser(app).register() for _ in range(5)]
    statuses = [db.get(User, u.id).verification_status for u in users]
    assert statuses[:3] == ["unverified"] * 3
    assert statuses[3:] == ["needs_verification"] * 2
    # Still allowed to trade/sign in: shared networks are normal.
    assert all(db.get(User, u.id).status == "active" for u in users)


def test_validation_errors_are_consistent_json(make_user):
    u = make_user()
    r = u.post("/api/orders", json={"asset_id": 1, "side": "buy"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR" and "request_id" in err and "Traceback" not in r.text
