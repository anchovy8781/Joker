"""Required test 16: abnormal cancellation and wash-trading patterns are detected,
explained, and handled proportionately (no automatic permanent bans)."""

from sqlalchemy import select, text

from app.models import RiskEvent, User
from app.services import risk
from tests.conftest import give_shares


def _events(db, uid=None, etype=None):
    q = select(RiskEvent)
    if uid is not None:
        q = q.where(RiskEvent.user_id == uid)
    if etype:
        q = q.where(RiskEvent.event_type == etype)
    db.expire_all()
    return db.execute(q).scalars().all()


def test_16_excessive_cancellation_detected(make_user, bare_asset, db):
    u = make_user()
    for _ in range(25):
        oid = u.order(bare_asset, "buy", 1, price=9_800).json()["order"]["id"]
        u.post(f"/api/orders/{oid}/cancel")
    risk.scan(db)
    db.commit()
    ev = _events(db, u.id, "excessive_cancellation")
    assert len(ev) == 1 and ev[0].severity == "medium"
    assert "25 of 25 orders canceled" in ev[0].reason and ev[0].evidence_json["canceled_1h"] == 25
    # Re-scanning in the same hour does not duplicate the event
    risk.scan(db)
    db.commit()
    assert len(_events(db, u.id, "excessive_cancellation")) == 1


def test_16b_spoofing_pattern_detected(make_user, bare_asset, db):
    u = make_user()
    for _ in range(3):
        oid = u.order(bare_asset, "buy", 50, price=9_500).json()["order"]["id"]  # 475k notional each
        u.post(f"/api/orders/{oid}/cancel")
    risk.scan(db)
    db.commit()
    ev = _events(db, u.id, "spoofing_pattern")
    assert len(ev) == 1 and ev[0].severity == "high"


def test_16c_wash_trading_between_linked_accounts(make_user, bare_asset, db):
    # Both accounts were created from the same (test client) network -> linked.
    a, b = make_user(), make_user()
    give_shares(db, a.id, bare_asset, 20)
    for _ in range(4):
        a.order(bare_asset, "sell", 1, price=10_000)
        b.order(bare_asset, "buy", 1, price=10_000)
    ev = _events(db, a.id, "related_account_trading")
    assert ev and ev[0].severity == "high"
    assert "same_signup_network" in ev[0].evidence_json["links"]
    assert "linked accounts" in ev[0].reason
    db.expire_all()
    # high risk (6) alone is below the hold threshold (8): orders still allowed
    assert db.get(User, a.id).risk_level == "medium"


def test_16d_repeated_wash_trading_escalates_to_restriction_and_appeal(make_user, make_admin, bare_asset, db):
    a, b = make_user(), make_user()
    give_shares(db, a.id, bare_asset, 30)
    for _ in range(10):
        a.order(bare_asset, "sell", 1, price=10_000)
        b.order(bare_asset, "buy", 1, price=10_000)
    db.expire_all()
    ua = db.get(User, a.id)
    assert ua.status == "restricted" and ua.risk_level == "high"
    r = a.order(bare_asset, "sell", 1, price=10_000)
    assert r.status_code in (403, 423)
    # The user can see why, and appeal
    mine = a.get("/api/account/risk").json()
    assert any(e["event_type"] == "wash_trading_related_accounts" for e in mine["events"])
    ap = a.post("/api/appeals", json={"message": "These are two different people sharing a dorm network."})
    assert ap.status_code == 201
    admin = make_admin()
    r = admin.post(f"/api/admin/appeals/{ap.json()['id']}/resolve",
                   json={"status": "accepted", "resolution": "verified separate people"})
    assert r.status_code == 200
    db.expire_all()
    ua = db.get(User, a.id)
    assert ua.status == "active" and ua.risk_level == "low"
    assert a.order(bare_asset, "sell", 1, price=10_000).status_code == 201


def test_unrelated_accounts_need_more_evidence(make_user, bare_asset, db):
    a, b = make_user(), make_user()
    # Make them unrelated (different networks)
    for uid, h in ((a.id, "net-a"), (b.id, "net-b")):
        db.get(User, uid).signup_ip_hash = h
    db.execute(text("UPDATE sessions SET ip_hash = 'x' || user_id"))
    db.commit()
    give_shares(db, a.id, bare_asset, 10)
    for _ in range(4):
        a.order(bare_asset, "sell", 1, price=10_000)
        b.order(bare_asset, "buy", 1, price=10_000)
    assert _events(db, a.id) == []  # 4 trades between strangers: nothing
    a.order(bare_asset, "sell", 1, price=10_000)
    b.order(bare_asset, "buy", 1, price=10_000)
    ev = _events(db, a.id)
    assert [e.event_type for e in ev] == ["repeated_pair_trading"] and ev[0].severity == "medium"


def test_high_risk_orders_held_for_review(make_user, make_admin, asset, db):
    u = make_user()
    user = db.get(User, u.id)
    user.risk_level = "high"
    db.commit()
    r = u.order(asset, "buy", 1)
    assert r.status_code == 423 and r.json()["error"]["code"] == "ORDER_HELD_FOR_REVIEW"


def test_medium_risk_gets_tighter_rate_limit(make_user, bare_asset, db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "rl_orders_per_user_medium_risk", (3, 60))
    u = make_user()
    db.get(User, u.id).risk_level = "medium"
    db.commit()
    codes = [u.order(bare_asset, "buy", 1, price=9_900).status_code for _ in range(4)]
    assert codes == [201, 201, 201, 429]


def test_admin_dismiss_lowers_risk(make_user, make_admin, bare_asset, db):
    u = make_user()
    for _ in range(25):
        oid = u.order(bare_asset, "buy", 1, price=9_800).json()["order"]["id"]
        u.post(f"/api/orders/{oid}/cancel")
    risk.scan(db)
    db.commit()
    db.expire_all()
    assert db.get(User, u.id).risk_level == "medium"
    admin = make_admin()
    ev = admin.get("/api/admin/risk-events?reviewed=false").json()
    target = next(e for e in ev if e["user_id"] == u.id)
    r = admin.post(f"/api/admin/risk-events/{target['id']}/review",
                   json={"decision": "dismiss", "resolution": "market maker style testing, fine"})
    assert r.status_code == 200
    db.expire_all()
    assert db.get(User, u.id).risk_level == "low"
