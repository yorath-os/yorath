"""Audit types of pre-public hardening: a model moved, a node over its limits.

Revision ID: 0004_hardening_audit
Revises: 0003_node_host_report
Create Date: 2026-11-16
"""

from alembic import op

revision = "0004_hardening_audit"
down_revision = "0003_node_host_report"
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
NEW_AUDIT_TYPES = ("model.moved", "node.over_limit")


def _audit_check(types: tuple[str, ...], *, valid: bool = True) -> None:
    values = ", ".join(f"'{t}'" for t in types)
    op.execute("ALTER TABLE audit_events DROP CONSTRAINT ck_audit_events_audit_event_type")
    op.execute(
        "ALTER TABLE audit_events ADD CONSTRAINT ck_audit_events_audit_event_type "
        f"CHECK (event_type IN ({values}))" + ("" if valid else " NOT VALID")
    )


def upgrade() -> None:
    _audit_check(AUDIT_TYPES + NEW_AUDIT_TYPES)


def downgrade() -> None:
    # the audit log is append-only: rows of the new types stay, so the old check is not validated
    _audit_check(AUDIT_TYPES, valid=False)
