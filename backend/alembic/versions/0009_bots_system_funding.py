"""Virtual traders (role 'bot') and system liquidity funding.

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

OLD_TYPES = ("'signup_grant','trade_buy','trade_sell','trade_fee','admin_adjustment','correction',"
             "'mission_reward','donation_sent','donation_received','admin_grant','listing_fee','listing_fee_refund'")


def upgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", "role IN ('user','admin','system','bot')")
    op.drop_constraint("ck_ledger_entry_type", "ledger_entries", type_="check")
    op.create_check_constraint("ck_ledger_entry_type", "ledger_entries",
                               f"entry_type IN ({OLD_TYPES},'system_funding')")


def downgrade() -> None:
    op.drop_constraint("ck_ledger_entry_type", "ledger_entries", type_="check")
    op.create_check_constraint("ck_ledger_entry_type", "ledger_entries", f"entry_type IN ({OLD_TYPES})")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", "role IN ('user','admin','system')")
