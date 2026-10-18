import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.api import account, admin, auth, internal, market, orders, social
from app import fastpath
from app.config import get_settings
from app.errors import AppError, install_error_handlers
from app.security import client_ip, ip_hash

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _check_production_settings(s) -> None:
    problems = []
    if s.ip_hash_secret in ("", "change-me-in-env") or len(s.ip_hash_secret) < 32:
        problems.append("YEX_IP_HASH_SECRET must be a random value of 32+ characters")
    if not s.cookie_secure:
        problems.append("YEX_COOKIE_SECURE must be true (HTTPS only)")
    if any(o.startswith("http://") for o in s.cors_origins):
        problems.append("YEX_CORS_ORIGINS must only contain https:// origins")
    if "yex_dev_password" in s.database_url:
        problems.append("YEX_DATABASE_URL uses the development password")
    if s.tick_secret and len(s.tick_secret) < 32:
        problems.append("YEX_TICK_SECRET must be 32+ characters")
    if problems:
        raise RuntimeError("Refusing to start in production: " + "; ".join(problems))


def _refresh_runtime_settings() -> None:
    """Pick up administrator switches (cheap: re-read at most every few seconds)."""
    from app.db import session_factory
    from app.services import runtime_settings

    if get_settings().env == "test":
        return
    db = session_factory()()
    try:
        runtime_settings.refresh(db)
    except Exception:  # never fail a request because settings could not be read
        logging.getLogger("yex").warning("could not refresh runtime settings", exc_info=True)
    finally:
        db.close()


def create_app() -> FastAPI:
    s = get_settings()
    if s.is_production:
        _check_production_settings(s)
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        from app.services import simulator

        simulator.start()  # virtual traders (no-op in tests or when disabled)
        yield

    app = FastAPI(
        lifespan=lifespan,
        title="YouTube Exchange API",
        version="0.1.0",
        description="Virtual-credit stock market game based on YouTube channels. Credits have no cash value.",
        docs_url=None if s.is_production else "/api/docs",
        openapi_url=None if s.is_production else "/api/openapi.json",
        redoc_url=None,
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
        path = request.url.path
        if path.startswith("/api/") and s.env != "test":
            allowed, retry = fastpath.memory_rate_limit(f"api:ip:{ip_hash(client_ip(request))}", *s.rl_api_per_ip)
            if not allowed:
                return JSONResponse(status_code=429, content={"error": {
                    "code": "RATE_LIMITED", "message": "Too many requests. Please slow down and try again later.",
                    "details": {"retry_after_seconds": retry}, "request_id": request.state.request_id}})
        if path.startswith("/api/"):
            fastpath.mark_view(path)
            await run_in_threadpool(_refresh_runtime_settings)
        ttl = fastpath.shared_ttl(path) if request.method == "GET" and s.env != "test" else None
        key = f"{path}?{request.url.query}"
        hit = fastpath.cache_get(key) if ttl else None
        if hit is not None:
            response = Response(content=hit[1], status_code=hit[0], media_type=hit[2])
        else:
            response = await call_next(request)
            if ttl and response.status_code == 200:
                body = b"".join([chunk async for chunk in response.body_iterator])
                fastpath.cache_put(key, ttl, 200, body, response.media_type or "application/json")
                response = Response(content=body, status_code=200, media_type=response.media_type or "application/json")
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if s.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Requested-With", "X-Request-ID"],
    )

    for r in (auth.router, market.router, account.router, orders.router, social.router, admin.router,
              internal.router):
        app.include_router(r)

    @app.get("/api/ping")
    def ping():
        # Readable cross-origin (no cookies) so the Android launcher can tell this server
        # apart from a hosting provider's "Not Found" page before navigating to it.
        return JSONResponse({"app": "youtube-exchange"}, headers={"Access-Control-Allow-Origin": "*"})

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
