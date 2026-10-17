"""Authentication, session cookies, CSRF protection, IP hashing and rate limiting."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, Request, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, get_engine
from app.errors import AppError
from app.models import User, UserSession

SESSION_COOKIE = "yex_session"
CSRF_COOKIE = "yex_csrf"
CSRF_HEADER = "x-csrf-token"
CLIENT_HEADER = "x-requested-with"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_hasher = PasswordHasher()  # Argon2id with library-recommended parameters
# Pre-computed hash used to equalise timing when the username does not exist.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def utcnow() -> datetime:
    return datetime.now(UTC)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str:
    """Client IP. With N trusted proxies, the real client is the N-th entry from the right of
    X-Forwarded-For; entries further left are client-controlled and therefore ignored."""
    hops = get_settings().trusted_proxy_hops
    if hops > 0:
        chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(chain) >= hops:
            return chain[-hops]
    return request.client.host if request.client else "unknown"


def ip_hash(ip: str) -> str:
    """Keyed hash so raw IP addresses are never stored."""
    secret = get_settings().ip_hash_secret.encode()
    return hmac.new(secret, ip.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- rate limiting


def hit_rate_limit(key: str, limit: int, window_seconds: int) -> int:
    """Fixed-window counter in PostgreSQL. Uses its own connection so it survives rollbacks."""
    now = utcnow()
    window_start = datetime.fromtimestamp(
        int(now.timestamp()) // window_seconds * window_seconds, UTC
    )
    with get_engine().begin() as conn:
        count = conn.execute(
            text(
                "INSERT INTO rate_limits (key, window_start, count) VALUES (:k, :w, 1) "
                "ON CONFLICT (key, window_start) DO UPDATE SET count = rate_limits.count + 1 "
                "RETURNING count"
            ),
            {"k": key, "w": window_start},
        ).scalar_one()
    if count > limit:
        raise AppError(
            429,
            "RATE_LIMITED",
            "Too many requests. Please slow down and try again later.",
            {"retry_after_seconds": window_seconds - int(now.timestamp()) % window_seconds},
        )
    return count


def peek_rate_limit(key: str, window_seconds: int) -> int:
    now = utcnow()
    window_start = datetime.fromtimestamp(
        int(now.timestamp()) // window_seconds * window_seconds, UTC
    )
    with get_engine().connect() as conn:
        return conn.execute(
            text("SELECT count FROM rate_limits WHERE key = :k AND window_start = :w"),
            {"k": key, "w": window_start},
        ).scalar() or 0


# ---------------------------------------------------------------- sessions


def create_session(db: Session, user: User, request: Request, response: Response) -> UserSession:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    session = UserSession(
        user_id=user.id,
        token_hash=token_digest(token),
        csrf_token=csrf,
        ip_hash=ip_hash(client_ip(request)),
        expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(session)
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    # Readable by JS on purpose: the client echoes it back in the X-CSRF-Token header.
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    return session


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _load_session(db: Session, request: Request) -> UserSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    session = db.execute(
        select(UserSession).where(UserSession.token_hash == token_digest(token))
    ).scalar_one_or_none()
    if session is None or session.revoked_at is not None or session.expires_at <= utcnow():
        return None
    return session


def require_client_header(request: Request) -> None:
    """Unsafe requests must carry a custom header, which cross-site forms cannot send
    without a CORS preflight. This protects unauthenticated endpoints (login CSRF)."""
    if request.method in UNSAFE_METHODS and request.headers.get(CLIENT_HEADER) != "yex":
        raise AppError(403, "CSRF_FAILED", "Missing client header")


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    session = _load_session(db, request)
    if session is None:
        return None
    request.state.session = session
    return db.get(User, session.user_id)


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    session = _load_session(db, request)
    if session is None:
        raise AppError(401, "UNAUTHENTICATED", "Login required")
    if request.method in UNSAFE_METHODS:
        require_client_header(request)
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent, session.csrf_token):
            raise AppError(403, "CSRF_FAILED", "Invalid CSRF token")
    user = db.get(User, session.user_id)
    if user is None or user.status == "suspended" or user.role == "system":
        raise AppError(403, "ACCOUNT_DISABLED", "Account is not allowed to sign in")
    request.state.session = session
    request.state.user_id = user.id
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise AppError(403, "FORBIDDEN", "Administrator permission required")
    return user
