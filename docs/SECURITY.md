# Security & Production Hardening

This document lists what the MVP already enforces and what an operator must add before
running it in production.

## Enforced by the application

| Area | Mechanism |
| --- | --- |
| Passwords | Argon2id (`argon2-cffi`), constant-time dummy verification for unknown users |
| Sessions | Random 256-bit token in an `HttpOnly`, `SameSite=Lax`, `Secure` (configurable) cookie; only its SHA-256 is stored; server-side revocation on logout; 7-day TTL |
| CSRF | Session-bound token (double-submit, compared in constant time) + mandatory `X-Requested-With: yex` header on every state-changing request (also protects login/register) |
| IDOR | Every account/order/trade query is scoped to the session user; foreign IDs return `404` (indistinguishable from non-existent) |
| Authorization | `require_admin` dependency on every `/api/admin/*` route; admins can only be created from the CLI |
| SQL injection | SQLAlchemy bound parameters everywhere; `LIKE` wildcards are escaped in search |
| XSS | React escapes all output; strict CSP on both the Next.js pages and the API (`default-src 'none'` for JSON) |
| Mass assignment | Pydantic request models use `extra="forbid"`: clients cannot send `fee`, `price`, `user_id`, balances, etc. |
| Rate limits | PostgreSQL fixed-window counters (survive rollbacks, shared by all workers): register/IP, login/IP, failed logins/user, orders/user (stricter for medium-risk users), all API/IP |
| Client IPs | Raw IPs are never stored; HMAC-SHA256 with `YEX_IP_HASH_SECRET`. `X-Forwarded-For` is ignored unless `YEX_TRUSTED_PROXY_HOPS` > 0, and then read from the right so client-forged entries are ignored |
| Errors | Uniform `{"error": {code, message, details, request_id}}`; stack traces only in server logs |
| Secrets in logs | Passwords, session tokens and the YouTube API key are never logged; provider errors never echo request URLs (which contain the key) |
| Economy integrity | DB `CHECK` constraints (non-negative cash, reserved ≤ cash/quantity, positive quantities), partial `UNIQUE` index for the one-time grant, append-only triggers on `ledger_entries`, `trades`, `audit_logs`, daily reconciliation |
| Production guard | With `YEX_ENV=production` the API refuses to start with a default IP-hash secret, insecure cookies, `http://` CORS origins or the development DB password; OpenAPI docs are disabled |

## Required before production

1. **HTTPS everywhere.** Terminate TLS at a reverse proxy (Caddy gets free Let's Encrypt
   certificates automatically). Set `YEX_COOKIE_SECURE=true`, `YEX_CORS_ORIGINS=["https://your.domain"]`,
   `YEX_TRUSTED_PROXY_HOPS=1`, and add HSTS at the proxy.
2. **Do not expose** PostgreSQL (`5432`) or the API container (`8000`) publicly. Only the proxy → `web:3000` path should be reachable.
3. **Database roles.** Run the app with a role that has no `UPDATE`/`DELETE`/`TRUNCATE`
   on `ledger_entries`, `trades`, `audit_logs` (the triggers are defence-in-depth, a
   table owner could drop them). Run migrations with a separate owner role. Example:
   ```sql
   REVOKE UPDATE, DELETE, TRUNCATE ON ledger_entries, trades, audit_logs FROM yex_app;
   ```
4. **Secrets.** Generate `YEX_IP_HASH_SECRET` and `POSTGRES_PASSWORD` randomly, keep them in
   a secret manager or the host's `.env` (never in git). Restrict the YouTube API key in
   Google Cloud Console to the *YouTube Data API v3* and to your server's IP.
5. **Backups.** Daily `pg_dump` (or WAL archiving) with tested restores. Ledger, trades
   and audit logs must be retained; see *Data retention* in the README.
6. **CAPTCHA (optional, free).** Set `YEX_TURNSTILE_SECRET` to require Cloudflare Turnstile
   for signups from networks that already created several accounts. The frontend widget is
   not wired up yet (see README → Not implemented).
7. **Email verification.** Not implemented (requires an email provider). Until then new
   accounts are `unverified`, multi-account networks become `needs_verification`.
8. **Monitoring.** Alert on: failed jobs (`/api/system/status`), reconciliation `mismatch`,
   critical risk events, rising 5xx rates, stale-channel count.
9. **Session hardening.** Consider shorter TTLs and rotating the session on privilege change;
   add 2FA for admin accounts.
10. **Dependency updates.** Versions are pinned; run `pip-audit` / `npm audit` regularly.
