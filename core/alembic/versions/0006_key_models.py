"""Models of an API key, new audit events of field-fixes-0-7.

``api_keys.models``: null — every chat model of the catalog (a new key), or a list of names, where
``local/*`` covers every local chat model. Keys issued before keep what they could reach: the local
models only, so the update doesn't open external models to them.

Revision ID: 0006_key_models
Revises: 0005_instance_channels
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_key_models"
down_revision = "0005_instance_channels"
branch_labels = None
depends_on = None

AUDIT_TYPES = (
    "execution.started",
    "execution.finished",
    "tool.called",
    "data.accessed",
    "policy.denied",
    "agent.invoked",
    "llm.called",
    "secret.issued",
    "secret.revoked",
    "egress.allowed",
    "egress.blocked",
    "sandbox.started",
    "sandbox.stopped",
    "outcome.recorded",
    "auth.login",
    "auth.login_failed",
    "auth.logout",
    "user.created",
    "user.updated",
    "document.uploaded",
    "document.deleted",
    "agent.updated",
    "agent.created",
    "installation.restored",
    "model.registered",
    "egress.unprotected",
    "egress.restored",
    "agent.connected",
    "agent.disconnected",
    "agent.removed",
    "agent.channel_changed",
    "agent.network_changed",
    "agent.version_changed",
    "node.joined",
    "node.approved",
    "node.revoked",
    "node.lost",
    "node.back",
    "model.placed",
    "model.removed",
    "node.classes_changed",
)
OLD_AUDIT_TYPES = AUDIT_TYPES + ("model.moved", "node.over_limit")
NEW_AUDIT_TYPES = (
    "api_key.models_changed",
    "agent.model_changed",
    "agent.restarted",
    "model.default_switched",
)


def _audit_check(types: tuple[str, ...], *, valid: bool = True) -> None:
    values = ", ".join(f"'{t}'" for t in types)
    op.execute("ALTER TABLE audit_events DROP CONSTRAINT ck_audit_events_audit_event_type")
    op.execute(
        "ALTER TABLE audit_events ADD CONSTRAINT ck_audit_events_audit_event_type "
        f"CHECK (event_type IN ({values}))" + ("" if valid else " NOT VALID")
    )


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("models", postgresql.JSONB(), nullable=True))
    op.execute("""UPDATE api_keys SET models = '["local/*"]'::jsonb""")
    _audit_check(OLD_AUDIT_TYPES + NEW_AUDIT_TYPES)


def downgrade() -> None:
    _audit_check(OLD_AUDIT_TYPES, valid=False)
    op.drop_column("api_keys", "models")
