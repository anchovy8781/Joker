"""Minute bars for intraday candle charts.

Revision ID: 0008
Revises: 0007
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_bars_interval", "market_bars", type_="check")
    op.create_check_constraint("ck_bars_interval", "market_bars", "interval IN ('1m','1h','1d')")


def downgrade() -> None:
    op.execute("DELETE FROM market_bars WHERE interval = '1m'")
    op.drop_constraint("ck_bars_interval", "market_bars", type_="check")
    op.create_check_constraint("ck_bars_interval", "market_bars", "interval IN ('1h','1d')")
