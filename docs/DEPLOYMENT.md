# Deployment (single host, HTTPS)

Everything here uses free software and free certificates. Nothing requires a paid plan.

```
Internet ──HTTPS──▶ Caddy (TLS, HSTS) ──▶ web:3000 (Next.js) ──/api──▶ api:8000 (FastAPI)
                                                                     worker ──▶ db (PostgreSQL)
```

## 1. Configure

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # → YEX_IP_HASH_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # → POSTGRES_PASSWORD
```

Set in `.env`:

```
YEX_ENV=production
YEX_COOKIE_SECURE=true
YEX_CORS_ORIGINS=["https://exchange.example.com"]
YEX_TRUSTED_PROXY_HOPS=1
```

## 2. Add an HTTPS reverse proxy

Create `docker-compose.prod.yml`:

```yaml
services:
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
    depends_on: [web]
  web:
    ports: !reset []        # only reachable through Caddy
  api:
    ports: !reset []
  db:
    ports: !reset []
volumes:
  caddy_data:
```

`Caddyfile`:

```
exchange.example.com {
    encode gzip
    header Strict-Transport-Security "max-age=63072000; includeSubDomains"
    reverse_proxy web:3000
}
```

Caddy obtains and renews a Let's Encrypt certificate automatically and appends the real
client address to `X-Forwarded-For` (hence `YEX_TRUSTED_PROXY_HOPS=1`).

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose exec api python -m app.cli create-admin <name>
```

## 3. Database privileges (recommended)

Run migrations as the owner role and the application as a restricted role:

```sql
CREATE ROLE yex_app LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE yex TO yex_app;
GRANT USAGE ON SCHEMA public TO yex_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO yex_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO yex_app;
REVOKE UPDATE, DELETE, TRUNCATE ON ledger_entries, trades, audit_logs FROM yex_app;
```

Point `migrate` at the owner URL and `api`/`worker` at the `yex_app` URL.

## 4. Operations

* Backups: `docker compose exec db pg_dump -U yex yex | gzip > backup-$(date +%F).sql.gz`
* Health: `GET /healthz` (API), `GET /api/system/status` (jobs, freshness, reconciliation)
* Scale: run more `worker` replicas safely (jobs are claimed with `FOR UPDATE SKIP LOCKED`
  and deduplicated). API workers are stateless.
