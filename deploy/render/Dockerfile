# One container for the free Render web service: Next.js (public port) + FastAPI (internal).
# Build context: repository root.  Scheduled jobs run via POST /api/internal/tick, which
# Supabase pg_cron calls every minute (see deploy/supabase/setup.sql).

FROM node:22-slim AS web
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1 BACKEND_URL=http://127.0.0.1:8000
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx next build \
 && cp -r .next/static .next/standalone/.next/static \
 && cp -r public .next/standalone/public

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=web /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY backend/requirements.txt backend/
RUN pip install -r backend/requirements.txt
COPY backend/alembic.ini backend/
COPY backend/alembic backend/alembic
COPY backend/app backend/app
COPY deploy/phone/address_watcher.py deploy/render/start.sh ./
COPY --from=web /web/.next/standalone web/
RUN useradd --system --uid 10001 app && chown -R app /app && chmod +x start.sh
USER app
EXPOSE 10000
CMD ["./start.sh"]
