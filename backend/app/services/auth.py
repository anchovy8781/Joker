"""Registration, login and the one-time signup grant."""

import re
import uuid
from datetime import timedelta

import httpx
from fastapi import Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Account, LedgerEntry, RiskEvent, User
from app.security import (
    client_ip,
    create_session,
    hash_password,
    hit_rate_limit,
    ip_hash,
    peek_rate_limit,
    utcnow,
    verify_password,
)
from app.services.audit import write_audit
from app.services.ledger import lock_account, post_entry

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
SIGNUPS_PER_NETWORK_BEFORE_REVIEW = 3


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_credentials(username: str, password: str) -> None:
    if not USERNAME_RE.match(username):
        raise AppError(422, "INVALID_USERNAME", "Username must be 3-20 letters, digits or _")
    if not (10 <= len(password) <= 128):
        raise AppError(422, "WEAK_PASSWORD", "Password must be 10-128 characters")
    if password.lower() == username.lower():
        raise AppError(422, "WEAK_PASSWORD", "Password must differ from the username")


def grant_signup_bonus(db: Session, user_id: int, request_id: str | None = None) -> bool:
    """Grant the one-time signup credits. Returns False if already granted.

    Protected twice: an explicit check under the account row lock, and a partial
    UNIQUE index (one 'signup_grant' ledger row per user) at the database level.
    """
    account = lock_account(db, user_id)
    already = db.execute(
        select(LedgerEntry.id).where(
            LedgerEntry.user_id == user_id, LedgerEntry.entry_type == "signup_grant"
        )
    ).first()
    if already:
        return False
    try:
        with db.begin_nested():
            post_entry(
                db,
                account,
                entry_type="signup_grant",
                amount=get_settings().signup_grant,
                transaction_id=uuid.uuid4(),
                reference_type="user",
                reference_id=user_id,
                memo="One-time signup grant (no cash value)",
            )
            db.flush()
    except IntegrityError:
        db.refresh(account)
        return False
    write_audit(db, "economy.signup_grant", target_type="user", target_id=user_id,
                request_id=request_id, metadata={"amount": get_settings().signup_grant})
    return True


def _verify_turnstile(token: str | None, ip: str) -> bool:
    secret = get_settings().turnstile_secret
    if not secret:
        return True
    if not token:
        return False
    try:
        r = httpx.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": secret, "response": token, "remoteip": ip},
            timeout=5,
        )
        return bool(r.json().get("success"))
    except (httpx.HTTPError, ValueError):
        return False


def register(
    db: Session,
    request: Request,
    response: Response,
    username: str,
    password: str,
    captcha_token: str | None = None,
) -> User:
    s = get_settings()
    ip = client_ip(request)
    iph = ip_hash(ip)
    hit_rate_limit(f"register:ip:{iph}", *s.rl_register_per_ip)
    validate_credentials(username, password)

    since = utcnow() - timedelta(hours=24)
    recent_from_network = db.execute(
        select(func.count(User.id)).where(User.signup_ip_hash == iph, User.created_at >= since)
    ).scalar_one()
    suspicious = recent_from_network >= SIGNUPS_PER_NETWORK_BEFORE_REVIEW
    if suspicious and s.turnstile_secret and not _verify_turnstile(captcha_token, ip):
        raise AppError(
            403, "CAPTCHA_REQUIRED", "Additional verification is required", {"captcha": "turnstile"}
        )

    user = User(
        username=username,
        normalized_username=normalize_username(username),
        password_hash=hash_password(password),
        role="user",
        signup_ip_hash=iph,
        # Shared networks are normal (schools, offices): flag for verification, never block.
        verification_status="needs_verification" if suspicious else "unverified",
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "USERNAME_TAKEN", "Username is already taken")
    db.add(Account(user_id=user.id, cash_balance=0, reserved_cash=0))
    db.flush()
    request_id = getattr(request.state, "request_id", None)
    grant_signup_bonus(db, user.id, request_id)
    if suspicious:
        db.add(
            RiskEvent(
                user_id=user.id,
                event_type="multi_account_signup",
                severity="low",
                score=1,
                reason=f"{recent_from_network + 1} accounts created from the same network in 24h",
                evidence_json={"signups_24h_same_network": recent_from_network + 1},
            )
        )
    write_audit(db, "auth.register", actor_id=user.id, target_type="user", target_id=user.id,
                request_id=request_id, metadata={"flagged": suspicious})
    user.last_login_at = utcnow()
    create_session(db, user, request, response)
    return user


def login(db: Session, request: Request, response: Response, username: str, password: str) -> User:
    s = get_settings()
    iph = ip_hash(client_ip(request))
    hit_rate_limit(f"login:ip:{iph}", *s.rl_login_per_ip)
    norm = normalize_username(username)
    fail_key = f"login:fail:{norm}"
    if peek_rate_limit(fail_key, s.rl_login_fail_per_user[1]) >= s.rl_login_fail_per_user[0]:
        raise AppError(429, "RATE_LIMITED", "Too many failed attempts. Try again later.")
    user = db.execute(select(User).where(User.normalized_username == norm)).scalar_one_or_none()
    ok = verify_password(user.password_hash if user else None, password)
    if not ok or user is None or user.role == "system":
        hit_rate_limit(fail_key, 10**9, s.rl_login_fail_per_user[1])  # count the failure only
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid username or password")
    if user.status == "suspended":
        raise AppError(403, "ACCOUNT_DISABLED", "This account is suspended")
    user.last_login_at = utcnow()
    create_session(db, user, request, response)
    write_audit(db, "auth.login", actor_id=user.id, target_type="user", target_id=user.id,
                request_id=getattr(request.state, "request_id", None))
    return user


def ensure_account(db: Session, user_id: int) -> Account:
    acct = db.get(Account, user_id)
    if acct is None:
        acct = Account(user_id=user_id, cash_balance=0, reserved_cash=0)
        db.add(acct)
        db.flush()
    return acct
