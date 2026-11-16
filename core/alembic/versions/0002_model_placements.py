"""Model catalog, model servers and placements (decision 44, change model-placements).

``model_resources.node_id`` becomes a placement: a model on a model server. ``local/default``
becomes a pointer (``alias_of``) at the catalog model ``local/<Ollama id>`` that it used to be.

Revision ID: 0002_model_placements
Revises: 0001_initial
Create Date: 2026-11-14
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_model_placements"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

ALL_CLASSES = "{public,internal,confidential,restricted}"
KEPT = ("local/default", "local/embeddings")
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
)
NEW_AUDIT_TYPES = ("model.placed", "model.removed", "node.classes_changed")


def _audit_check(types: tuple[str, ...], *, valid: bool = True) -> None:
    values = ", ".join(f"'{t}'" for t in types)
    op.execute("ALTER TABLE audit_events DROP CONSTRAINT ck_audit_events_audit_event_type")
    op.execute(
        "ALTER TABLE audit_events ADD CONSTRAINT ck_audit_events_audit_event_type "
        f"CHECK (event_type IN ({values}))" + ("" if valid else " NOT VALID")
    )


def upgrade() -> None:
    _audit_check(AUDIT_TYPES + NEW_AUDIT_TYPES)
    op.add_column(
        "nodes",
        sa.Column(
            "allowed_data_classes", postgresql.ARRAY(sa.String(32)), nullable=False,
            server_default=ALL_CLASSES,
        ),
    )  # fmt: skip
    op.add_column("model_resources", sa.Column("alias_of", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_model_resources_alias_of_model_resources", "model_resources", "model_resources",
        ["alias_of"], ["id"],
    )  # fmt: skip
    op.create_table(
        "model_servers",
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("gpu_indexes", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("slots", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_servers"),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], name="fk_model_servers_node_id_nodes"),
        sa.UniqueConstraint("node_id", "name", name="uq_model_servers_node_id_name"),
    )
    op.create_table(
        "model_placements",
        sa.Column("model_resource_id", sa.Uuid(), nullable=False),
        sa.Column("model_server_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("status_detail", sa.String(500), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_placements"),
        sa.ForeignKeyConstraint(
            ["model_resource_id"], ["model_resources.id"],
            name="fk_model_placements_model_resource_id_model_resources",
        ),
        sa.ForeignKeyConstraint(
            ["model_server_id"], ["model_servers.id"],
            name="fk_model_placements_model_server_id_model_servers",
        ),
        sa.UniqueConstraint(
            "model_resource_id", "model_server_id",
            name="uq_model_placements_model_resource_id_model_server_id",
        ),
        sa.CheckConstraint(
            "status IN ('pulling', 'ready', 'failed')",
            name="ck_model_placements_placement_status",
        ),
    )  # fmt: skip
    bind = op.get_bind()

    # model servers of the nodes that already exist
    servers: dict[uuid.UUID, list[uuid.UUID]] = {}
    main_id = None
    nodes = bind.execute(
        sa.text("SELECT id, role, gpu_count, has_model FROM nodes ORDER BY created_at")
    ).all()
    for node_id, role, gpu_count, has_model in nodes:
        if role == "main":
            main_id = main_id or node_id
            specs = [("ollama", list(range(gpu_count or 0)))]
        elif gpu_count:
            specs = [(f"gpu{i}", [i]) for i in range(gpu_count)]
        elif has_model:
            specs = [("cpu", [])]
        else:
            specs = []
        for name, gpus in specs:
            server_id = uuid.uuid7()
            bind.execute(
                sa.text(
                    "INSERT INTO model_servers (id, node_id, name, gpu_indexes) "
                    "VALUES (:id, :node, :name, :gpus)"
                ),
                {"id": server_id, "node": node_id, "name": name, "gpus": gpus},
            )
            servers.setdefault(node_id, []).append(server_id)

    # local/default becomes a pointer at the catalog model it used to be
    row = bind.execute(
        sa.text(
            "SELECT id, model_id, allowed_data_classes, input_price_per_mtok, "
            "output_price_per_mtok, context_length, node_id FROM model_resources "
            "WHERE name = 'local/default' AND provider = 'ollama'"
        )
    ).first()
    if row is not None:
        alias_id, model_id, classes, price_in, price_out, window, node_id = row
        name = f"local/{model_id}"[:100]
        target = bind.execute(
            sa.text("SELECT id FROM model_resources WHERE name = :name"), {"name": name}
        ).scalar()
        if target is None:
            target = uuid.uuid7()
            bind.execute(
                sa.text(
                    "INSERT INTO model_resources (id, name, provider, model_id, "
                    "allowed_data_classes, input_price_per_mtok, output_price_per_mtok, "
                    "context_length) VALUES (:id, :name, 'ollama', :model_id, :classes, "
                    ":price_in, :price_out, :window)"
                ),
                {"id": target, "name": name, "model_id": model_id, "classes": classes,
                 "price_in": price_in, "price_out": price_out, "window": window},
            )  # fmt: skip
        bind.execute(
            sa.text("UPDATE model_resources SET alias_of = :target WHERE id = :id"),
            {"target": target, "id": alias_id},
        )
        home = servers.get(node_id or main_id, [])
        if home:
            bind.execute(
                sa.text(
                    "INSERT INTO model_placements (id, model_resource_id, model_server_id, "
                    "status, progress, updated_at) VALUES (:id, :model, :server, 'ready', 100, "
                    "now())"
                ),
                {"id": uuid.uuid7(), "model": target, "server": home[0]},
            )
    op.drop_constraint("fk_model_resources_node_id_nodes", "model_resources", type_="foreignkey")
    op.drop_column("model_resources", "node_id")


def downgrade() -> None:
    # the audit log is append-only: rows of the new types stay, so the old check is not validated
    _audit_check(AUDIT_TYPES, valid=False)
    op.add_column("model_resources", sa.Column("node_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_model_resources_node_id_nodes", "model_resources", "nodes", ["node_id"], ["id"]
    )
    bind = op.get_bind()
    # the node of the pointer target's placement, unless it is the main node
    bind.execute(
        sa.text(
            "UPDATE model_resources m SET node_id = n.id FROM model_placements p "
            "JOIN model_servers s ON s.id = p.model_server_id JOIN nodes n ON n.id = s.node_id "
            "WHERE p.model_resource_id = m.alias_of AND n.role <> 'main' AND p.status = 'ready'"
        )
    )
    op.drop_table("model_placements")
    op.drop_table("model_servers")
    op.drop_constraint(
        "fk_model_resources_alias_of_model_resources", "model_resources", type_="foreignkey"
    )
    op.drop_column("model_resources", "alias_of")
    # catalog rows stay, disabled: executions that used a model keep their reference to it
    bind.execute(
        sa.text(
            "UPDATE model_resources SET enabled = false WHERE provider = 'ollama' "
            "AND name LIKE 'local/%' AND name NOT IN :kept"
        ).bindparams(sa.bindparam("kept", expanding=True)),
        {"kept": list(KEPT)},
    )
    op.drop_column("nodes", "allowed_data_classes")
