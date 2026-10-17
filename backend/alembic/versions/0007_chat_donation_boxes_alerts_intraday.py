"""Chat donation boxes and tips, price alerts, intraday fair-value adjustment.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

NEW_TABLES = ("donation_boxes", "price_alerts")


def upgrade() -> None:
    op.create_table(
        "donation_boxes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("room", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=60), nullable=False),
        sa.Column("goal", sa.BigInteger(), nullable=False),
        sa.Column("raised", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=8), server_default="open", nullable=False),
        sa.Column("kst_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("goal > 0", name="ck_dbox_goal"),
        sa.CheckConstraint("raised >= 0 AND raised <= goal", name="ck_dbox_raised"),
        sa.CheckConstraint("status IN ('open','closed')", name="ck_dbox_status"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        # One donation box per user per Korean day, enforced by the database.
        sa.UniqueConstraint("owner_id", "kst_date", name="uq_dbox_owner_day"),
    )
    op.create_index("ix_dbox_room", "donation_boxes", ["room", "id"])
    op.add_column("donations", sa.Column("box_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_donations_box", "donations", "donation_boxes", ["box_id"], ["id"])
    op.create_index("ix_donations_box", "donations", ["box_id"])

    op.add_column("chat_messages", sa.Column("kind", sa.String(length=16), server_default="text", nullable=False))
    op.add_column("chat_messages", sa.Column("ref_id", sa.BigInteger(), nullable=True))
    op.create_check_constraint("ck_chat_kind", "chat_messages", "kind IN ('text','donation_box','donation')")

    op.create_table(
        "price_alerts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column("direction", sa.String(length=5), nullable=False),
        sa.Column("target_price", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("direction IN ('above','below')", name="ck_alert_direction"),
        sa.CheckConstraint("target_price > 0", name="ck_alert_price"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["market_assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alerts_active", "price_alerts", ["asset_id"],
                    postgresql_where=sa.text("triggered_at IS NULL AND canceled_at IS NULL"))
    op.create_index("ix_alerts_user", "price_alerts", ["user_id", "id"])

    op.add_column("market_assets", sa.Column("intraday_adj_bps", sa.Integer(), server_default="0", nullable=False))
    op.add_column("market_assets", sa.Column("intraday_detail", postgresql.JSONB(), nullable=True))
    op.add_column("market_assets", sa.Column("intraday_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_assets_intraday_adj", "market_assets", "intraday_adj_bps BETWEEN -1000 AND 1000")

    tables = ", ".join(f"'{t}'" for t in NEW_TABLES)
    op.execute(f"""
    DO $$ DECLARE t text; BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        FOREACH t IN ARRAY ARRAY[{tables}] LOOP
          EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', t);
          EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        END LOOP;
      END IF;
    END $$;
    """)


def downgrade() -> None:
    op.drop_constraint("ck_assets_intraday_adj", "market_assets", type_="check")
    for c in ("intraday_updated_at", "intraday_detail", "intraday_adj_bps"):
        op.drop_column("market_assets", c)
    op.drop_index("ix_alerts_user", table_name="price_alerts")
    op.drop_index("ix_alerts_active", table_name="price_alerts")
    op.drop_table("price_alerts")
    op.drop_constraint("ck_chat_kind", "chat_messages", type_="check")
    op.drop_column("chat_messages", "ref_id")
    op.drop_column("chat_messages", "kind")
    op.drop_index("ix_donations_box", table_name="donations")
    op.drop_constraint("fk_donations_box", "donations", type_="foreignkey")
    op.drop_column("donations", "box_id")
    op.drop_index("ix_dbox_room", table_name="donation_boxes")
    op.drop_table("donation_boxes")
