"""변동성완화장치 (VI): per-asset call-auction state, and the tape fill stays out of a VI.

Revision ID: 0013
Revises: 0012
"""
import os
import runpy

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

_PREV = runpy.run_path(os.path.join(os.path.dirname(__file__), "0012_bar_turnover.py"))["FUNCTION"]
_OLD = "IF NOT FOUND OR a.trading_status <> 'active' THEN RETURN NULL; END IF;"
_NEW = ("IF NOT FOUND OR a.trading_status <> 'active' OR (a.vi_until IS NOT NULL) THEN RETURN NULL; END IF;"
        "   -- in a VI: the full engine collects the order for the call auction")
assert _OLD in _PREV
FUNCTION = _PREV.replace(_OLD, _NEW)


def upgrade() -> None:
    op.execute("ALTER TABLE market_assets ADD COLUMN IF NOT EXISTS vi_until timestamptz")
    op.execute("ALTER TABLE market_assets ADD COLUMN IF NOT EXISTS vi_kind varchar(8)")
    op.execute("ALTER TABLE market_assets ADD COLUMN IF NOT EXISTS vi_base bigint")
    op.execute("ALTER TABLE market_assets ADD COLUMN IF NOT EXISTS vi_last_at timestamptz")
    op.execute(FUNCTION)


def downgrade() -> None:
    op.execute(_PREV)
    for c in ("vi_last_at", "vi_base", "vi_kind", "vi_until"):
        op.execute(f"ALTER TABLE market_assets DROP COLUMN IF EXISTS {c}")
