import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api import account, admin, auth, market, orders
from app.config import get_settings
from app.errors import AppError, install_error_handlers
from app.security import client_ip, hit_rate_limit, ip_hash

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
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
        if request.url.path.startswith("/api/") and s.env != "test":
            try:
                key = f"api:ip:{ip_hash(client_ip(request))}"
                await run_in_threadpool(hit_rate_limit, key, *s.rl_api_per_ip)
            except AppError as exc:
                return JSONResponse(status_code=429, content={"error": {
                    "code": exc.code, "message": exc.message, "details": exc.details,
                    "request_id": request.state.request_id}})
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if s.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Requested-With", "X-Request-ID"],
    )

    for r in (auth.router, market.router, account.router, orders.router, admin.router):
        app.include_router(r)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
