FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

RUN useradd --system --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
# --proxy-headers is NOT enabled: client IPs are resolved by YEX_TRUSTED_PROXY_HOPS instead.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
