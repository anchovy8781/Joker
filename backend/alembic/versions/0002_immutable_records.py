"""Block UPDATE/DELETE on ledger, trades and audit logs (append-only records).

Corrections must be recorded as new, opposite-direction entries. In production the
application role should additionally lack UPDATE/DELETE/TRUNCATE privileges on these
tables (see docs/SECURITY.md).

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = ("ledger_entries", "trades", "audit_logs")


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION yex_forbid_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'table % is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION yex_forbid_mutation();"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table};")
    op.execute("DROP FUNCTION IF EXISTS yex_forbid_mutation();")
