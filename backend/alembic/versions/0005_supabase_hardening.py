"""Supabase hardening: keep the exchange tables out of Supabase's auto-generated Data API.

Supabase publishes the `public` schema through PostgREST to its `anon`/`authenticated`
roles. This app never uses that API (all access goes through the FastAPI server), so:
  * revoke every privilege those roles have on our tables, sequences and functions
    (also for objects created later by this role), and
  * enable row level security on every table (no policies => the Data API sees nothing).
The app connects as the table owner, which RLS does not restrict.
On plain PostgreSQL (no `anon` role) this migration does nothing.

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLES = [
    "users", "sessions", "accounts", "ledger_entries", "channels", "market_assets", "positions",
    "orders", "trades", "market_bars", "channel_metrics", "market_events", "price_updates",
    "risk_events", "audit_logs", "jobs", "rate_limits", "reconciliation_runs", "announcements",
    "reports", "appeals", "api_quota_usage", "chat_messages", "notifications", "user_missions",
    "popularity_history", "auto_orders", "donations", "alembic_version",
]


def upgrade() -> None:
    tables = ", ".join(f"'{t}'" for t in TABLES)
    op.execute(f"""
    DO $$
    DECLARE t text;
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
        REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated;
        FOREACH t IN ARRAY ARRAY[{tables}] LOOP
          EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        END LOOP;
      END IF;
    END $$;
    """)


def downgrade() -> None:
    pass  # intentionally not re-opening access
