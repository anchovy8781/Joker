"""Listing fee, one-time admin grant, intraday channel samples for the live chart.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

OLD_TYPES = ("'signup_grant','trade_buy','trade_sell','trade_fee','admin_adjustment','correction',"
             "'mission_reward','donation_sent','donation_received'")
NEW_TYPES = OLD_TYPES + ",'admin_grant','listing_fee','listing_fee_refund'"


def upgrade() -> None:
    op.drop_constraint("ck_ledger_entry_type", "ledger_entries", type_="check")
    op.create_check_constraint("ck_ledger_entry_type", "ledger_entries", f"entry_type IN ({NEW_TYPES})")
    op.create_index("uq_ledger_single_admin_grant", "ledger_entries", ["user_id"], unique=True,
                    postgresql_where=sa.text("entry_type = 'admin_grant'"))

    op.create_table(
        "channel_samples",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subscriber_count", sa.BigInteger(), nullable=True),
        sa.Column("view_count", sa.BigInteger(), nullable=False),
        sa.Column("video_count", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.CheckConstraint("source IN ('youtube_api','demo')", name="ck_samples_source"),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_samples_channel_obs", "channel_samples", ["channel_id", "observed_at"])
    # Same Supabase lockdown as 0005 for the new table (no-op on plain PostgreSQL).
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON public.channel_samples FROM anon, authenticated;
        ALTER TABLE public.channel_samples ENABLE ROW LEVEL SECURITY;
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.drop_index("ix_samples_channel_obs", table_name="channel_samples")
    op.drop_table("channel_samples")
    op.drop_index("uq_ledger_single_admin_grant", table_name="ledger_entries")
    op.drop_constraint("ck_ledger_entry_type", "ledger_entries", type_="check")
    op.create_check_constraint("ck_ledger_entry_type", "ledger_entries", f"entry_type IN ({OLD_TYPES})")
