"""The host scan report of a worker node (decision 36, change outside-agents-cluster).

Revision ID: 0003_node_host_report
Revises: 0002_model_placements
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_node_host_report"
down_revision = "0002_model_placements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("host_report", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "host_report")
