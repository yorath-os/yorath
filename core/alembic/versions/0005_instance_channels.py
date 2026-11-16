"""Channel checks of an Instance: the Telegram bot as the node sees it (change telegram-check).

Revision ID: 0005_instance_channels
Revises: 0004_hardening_audit
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005_instance_channels"
down_revision = "0004_hardening_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("instances", sa.Column("channels", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("instances", "channels")
