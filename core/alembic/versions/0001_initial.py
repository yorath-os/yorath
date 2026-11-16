# ruff: noqa: E501 — SQL statements are kept as pg_dump prints them
"""Initial schema of Yorath 0.4.0.

Squashes revisions 0001_init to 0020_cluster_nodes of the chain used before the product was renamed.
Installations older than 0.4.0 are not upgraded, so the chain starts again here. The statements are
the ``pg_dump --schema-only`` of the schema that the old chain produced; the seed rows are inserted
the same way the old revisions inserted them. The two schemas were compared with ``pg_dump`` when
the chain was squashed (change rename-yorath): equal except the column comments, now in English.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-13
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA: list[str] = [
    """
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public
    """,
    """
CREATE FUNCTION public.audit_events_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (% on %)', TG_OP, TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
    """,
    """
CREATE TABLE public.agent_revisions (
    agent_id uuid NOT NULL,
    revision_no integer NOT NULL,
    path character varying(500) NOT NULL,
    content_sha256 character varying(64) NOT NULL,
    front_matter jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
)
    """,
    """
CREATE TABLE public.agents (
    name character varying(100) NOT NULL,
    display_name character varying(200) NOT NULL,
    description text,
    owner_id uuid,
    hosting character varying(32) NOT NULL,
    mode character varying(32) NOT NULL,
    runtime_type_id uuid NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_agents_agent_mode CHECK (mode IN ('delegated', 'autonomous')),
    CONSTRAINT ck_agents_hosting CHECK (hosting IN ('managed', 'external'))
)
    """,
    """
CREATE TABLE public.api_keys (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    prefix character varying(32) NOT NULL,
    token_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    revoked_at timestamp with time zone,
    agent_id uuid,
    scope character varying(20) DEFAULT 'model'::character varying NOT NULL
)
    """,
    """
CREATE TABLE public.audit_events (
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    event_type character varying(32) NOT NULL,
    execution_id uuid,
    span_id character varying(16),
    actor_principal_id uuid,
    effective_principal_id uuid,
    agent_id uuid,
    agent_revision_id uuid,
    runtime_type character varying(50),
    model character varying(200),
    tool character varying(100),
    data_asset_id uuid,
    data_class character varying(32),
    decision character varying(32),
    reason text,
    cost_usd numeric(12,6),
    control_level character varying(32),
    parent_execution_id uuid,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    id uuid NOT NULL,
    CONSTRAINT ck_audit_events_audit_event_type CHECK (event_type IN ('execution.started', 'execution.finished', 'tool.called', 'data.accessed', 'policy.denied', 'agent.invoked', 'llm.called', 'secret.issued', 'secret.revoked', 'egress.allowed', 'egress.blocked', 'sandbox.started', 'sandbox.stopped', 'outcome.recorded', 'auth.login', 'auth.login_failed', 'auth.logout', 'user.created', 'user.updated', 'document.uploaded', 'document.deleted', 'agent.updated', 'agent.created', 'installation.restored', 'model.registered', 'egress.unprotected', 'egress.restored', 'agent.connected', 'agent.disconnected', 'agent.removed', 'agent.channel_changed', 'agent.network_changed', 'agent.version_changed', 'node.joined', 'node.approved', 'node.revoked', 'node.lost', 'node.back')),
    CONSTRAINT ck_audit_events_control_level CHECK (control_level IN ('tool', 'sandbox', 'gateway')),
    CONSTRAINT ck_audit_events_data_class CHECK (data_class IN ('public', 'internal', 'confidential', 'restricted')),
    CONSTRAINT ck_audit_events_decision CHECK (decision IN ('permit', 'deny'))
)
    """,
    """
CREATE TABLE public.chunks (
    document_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    ordinal integer NOT NULL,
    heading_path character varying(500) NOT NULL,
    text text NOT NULL,
    data_class character varying(32) NOT NULL,
    embedding public.vector(768) NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('russian'::regconfig, text)) STORED NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_chunks_data_class CHECK (data_class IN ('public', 'internal', 'confidential', 'restricted'))
)
    """,
    """
CREATE TABLE public.cluster_join_tokens (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    token_hash character varying(64) NOT NULL,
    created_by uuid,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    node_id uuid
)
    """,
    """
CREATE TABLE public.clusters (
    name character varying(100) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
)
    """,
    """
CREATE TABLE public.conversations (
    user_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    title character varying(200) NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
)
    """,
    """
CREATE TABLE public.data_assets (
    name character varying(100) NOT NULL,
    kind character varying(32) NOT NULL,
    data_class character varying(32) NOT NULL,
    owner_id uuid,
    description text,
    access_rules jsonb DEFAULT '{}'::jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    CONSTRAINT ck_data_assets_asset_kind CHECK (kind IN ('documents', 'records')),
    CONSTRAINT ck_data_assets_data_class CHECK (data_class IN ('public', 'internal', 'confidential', 'restricted'))
)
    """,
    """
CREATE TABLE public.deployments (
    agent_revision_id uuid NOT NULL,
    cluster_id uuid NOT NULL,
    desired_instances integer DEFAULT 1 NOT NULL,
    budget jsonb,
    enabled boolean DEFAULT true NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    network_enabled boolean DEFAULT true NOT NULL,
    runtime_version character varying(100),
    channel_hash character varying(64),
    settings jsonb DEFAULT '{}'::jsonb NOT NULL,
    node_id uuid
)
    """,
    """
CREATE TABLE public.documents (
    asset_id uuid NOT NULL,
    filename character varying(255) NOT NULL,
    content_type character varying(100) NOT NULL,
    size_bytes integer NOT NULL,
    sha256 character varying(64) NOT NULL,
    data_class character varying(32) NOT NULL,
    status character varying(32) NOT NULL,
    error text,
    text text,
    pages integer,
    uploaded_by uuid,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_documents_data_class CHECK (data_class IN ('public', 'confidential', 'internal', 'restricted')),
    CONSTRAINT ck_documents_document_status CHECK (status IN ('uploaded', 'parsed', 'indexing', 'indexed', 'failed'))
)
    """,
    """
COMMENT ON COLUMN public.documents.text IS 'extracted text used for chunking'
    """,
    """
CREATE TABLE public.egress_flows (
    id uuid NOT NULL,
    hour timestamp with time zone NOT NULL,
    node character varying(100) NOT NULL,
    source_ip character varying(64) NOT NULL,
    source character varying(200) NOT NULL,
    domain character varying(300) NOT NULL,
    requests integer NOT NULL,
    blocked integer NOT NULL,
    bytes bigint NOT NULL,
    first_at timestamp with time zone NOT NULL,
    last_at timestamp with time zone NOT NULL
)
    """,
    """
CREATE TABLE public.execution_attempts (
    execution_id uuid NOT NULL,
    attempt_no integer DEFAULT 1 NOT NULL,
    instance_id uuid,
    runtime_installation_id uuid,
    model_resource_id uuid,
    model character varying(200),
    runtime_session_id character varying(200),
    status character varying(32) NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    exit_code integer,
    error_message text,
    usage jsonb,
    cost_usd numeric(12,6),
    cost_estimated boolean DEFAULT false NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_execution_attempts_attempt_status CHECK (status IN ('running', 'succeeded', 'failed', 'timeout', 'config_error', 'cancelled'))
)
    """,
    """
COMMENT ON COLUMN public.execution_attempts.model IS 'model actually used'
    """,
    """
CREATE TABLE public.execution_tokens (
    execution_id uuid NOT NULL,
    token_hash character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
)
    """,
    """
CREATE TABLE public.executions (
    agent_revision_id uuid NOT NULL,
    deployment_id uuid,
    initiated_by_principal_id uuid NOT NULL,
    effective_principal_id uuid NOT NULL,
    parent_execution_id uuid,
    trigger character varying(32) NOT NULL,
    status character varying(32) NOT NULL,
    max_data_class_seen character varying(32),
    finished_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    api_key_id uuid,
    CONSTRAINT ck_executions_data_class CHECK (max_data_class_seen IN ('public', 'internal', 'confidential', 'restricted')),
    CONSTRAINT ck_executions_execution_status CHECK (status IN ('created', 'denied', 'queued', 'running', 'succeeded', 'failed', 'timeout', 'config_error', 'cancelled')),
    CONSTRAINT ck_executions_execution_trigger CHECK (trigger IN ('user_message', 'agent_call', 'event', 'schedule', 'api'))
)
    """,
    """
CREATE TABLE public.instances (
    deployment_id uuid NOT NULL,
    node_id uuid,
    runtime_installation_id uuid,
    status character varying(32) NOT NULL,
    external_ref character varying(200),
    started_at timestamp with time zone,
    stopped_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    status_detail character varying(500),
    reported_at timestamp with time zone,
    CONSTRAINT ck_instances_instance_status CHECK (status IN ('starting', 'running', 'stopped', 'failed', 'lost'))
)
    """,
    """
CREATE TABLE public.messages (
    conversation_id uuid NOT NULL,
    role character varying(32) NOT NULL,
    content text NOT NULL,
    execution_id uuid,
    citations jsonb DEFAULT '[]'::jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_messages_message_role CHECK (role IN ('user', 'assistant', 'system'))
)
    """,
    """
CREATE TABLE public.model_resources (
    name character varying(100) NOT NULL,
    provider character varying(32) NOT NULL,
    model_id character varying(200) NOT NULL,
    allowed_data_classes character varying(32)[] NOT NULL,
    input_price_per_mtok numeric(12,6),
    output_price_per_mtok numeric(12,6),
    is_default boolean DEFAULT false NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    context_length integer DEFAULT 4096 NOT NULL,
    node_id uuid,
    CONSTRAINT ck_model_resources_model_provider CHECK (provider IN ('ollama', 'openrouter'))
)
    """,
    """
CREATE TABLE public.nodes (
    cluster_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    cpu_count integer NOT NULL,
    ram_gb integer NOT NULL,
    gpu character varying(100),
    vram_gb integer,
    updated_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    gpu_count integer DEFAULT 0 NOT NULL,
    role character varying(16) DEFAULT 'main'::character varying NOT NULL,
    status character varying(16) DEFAULT 'active'::character varying NOT NULL,
    overlay_ip character varying(45),
    wg_public_key character varying(64),
    token_hash character varying(64),
    last_seen_at timestamp with time zone,
    has_model boolean DEFAULT false NOT NULL
)
    """,
    """
CREATE TABLE public.outcomes (
    execution_id uuid NOT NULL,
    kind character varying(32) NOT NULL,
    status character varying(32) NOT NULL,
    ref character varying(500),
    data jsonb DEFAULT '{}'::jsonb NOT NULL,
    confirmed_by uuid,
    confirmed_at timestamp with time zone,
    useful boolean,
    rated_by uuid,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_outcomes_outcome_kind CHECK (kind IN ('pull_request', 'answer')),
    CONSTRAINT ck_outcomes_outcome_status CHECK (status IN ('pending', 'accepted', 'rejected', 'merged'))
)
    """,
    """
COMMENT ON COLUMN public.outcomes.ref IS 'PR URL or response id'
    """,
    """
CREATE TABLE public.policies (
    name character varying(100) NOT NULL,
    cedar text NOT NULL,
    description text,
    enabled boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
)
    """,
    """
CREATE TABLE public.principals (
    kind character varying(32) NOT NULL,
    display_name character varying(200) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_principals_principal_kind CHECK (kind IN ('user', 'service'))
)
    """,
    """
CREATE TABLE public.records (
    asset_id uuid NOT NULL,
    key character varying(100) NOT NULL,
    label character varying(300) NOT NULL,
    manager_key character varying(100),
    department character varying(100),
    data jsonb NOT NULL,
    data_class character varying(32) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_records_data_class CHECK (data_class IN ('public', 'internal', 'confidential', 'restricted'))
)
    """,
    """
CREATE TABLE public.runtime_installations (
    runtime_type_id uuid NOT NULL,
    node_id uuid NOT NULL,
    version character varying(50) NOT NULL,
    path character varying(500),
    probed_at timestamp with time zone,
    streams_events boolean DEFAULT false NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
)
    """,
    """
CREATE TABLE public.runtime_types (
    name character varying(50) NOT NULL,
    display_name character varying(100) NOT NULL,
    id uuid NOT NULL
)
    """,
    """
CREATE TABLE public.secrets (
    name character varying(100) NOT NULL,
    ciphertext text NOT NULL,
    updated_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
)
    """,
    """
CREATE TABLE public.service_accounts (
    principal_id uuid NOT NULL,
    owner_user_id uuid NOT NULL,
    agent_id uuid NOT NULL
)
    """,
    """
CREATE TABLE public.system_settings (
    key character varying(100) NOT NULL,
    value jsonb NOT NULL,
    updated_at timestamp with time zone NOT NULL
)
    """,
    """
CREATE TABLE public.trace_spans (
    trace_id uuid NOT NULL,
    span_id character varying(16) NOT NULL,
    parent_span_id character varying(16),
    name character varying(200) NOT NULL,
    kind character varying(32) NOT NULL,
    execution_id uuid NOT NULL,
    attempt_id uuid,
    agent_id uuid,
    model character varying(200),
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    ended_at timestamp with time zone,
    status character varying(32) NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    raw jsonb,
    id uuid NOT NULL,
    CONSTRAINT ck_trace_spans_span_kind CHECK (kind IN ('execution', 'llm', 'tool', 'data', 'agent', 'runtime')),
    CONSTRAINT ck_trace_spans_span_status CHECK (status IN ('unset', 'ok', 'error'))
)
    """,
    """
COMMENT ON COLUMN public.trace_spans.raw IS 'raw runtime event'
    """,
    """
CREATE TABLE public.user_sessions (
    user_id uuid NOT NULL,
    token_hash character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone,
    revoked_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
)
    """,
    """
CREATE TABLE public.users (
    principal_id uuid NOT NULL,
    login character varying(100) NOT NULL,
    provider character varying(32) DEFAULT 'local'::character varying NOT NULL,
    password_hash character varying(255),
    role character varying(50) DEFAULT 'member'::character varying NOT NULL,
    department character varying(100),
    manager_id uuid,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL
)
    """,
    """
COMMENT ON COLUMN public.users.attributes IS 'extensible attributes: assigned_departments'
    """,
    """
ALTER TABLE ONLY public.agent_revisions
    ADD CONSTRAINT pk_agent_revisions PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.agents
    ADD CONSTRAINT pk_agents PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT pk_api_keys PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT pk_audit_events PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT pk_chunks PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.cluster_join_tokens
    ADD CONSTRAINT pk_cluster_join_tokens PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.clusters
    ADD CONSTRAINT pk_clusters PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT pk_conversations PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.data_assets
    ADD CONSTRAINT pk_data_assets PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.deployments
    ADD CONSTRAINT pk_deployments PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.documents
    ADD CONSTRAINT pk_documents PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.egress_flows
    ADD CONSTRAINT pk_egress_flows PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.execution_attempts
    ADD CONSTRAINT pk_execution_attempts PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.execution_tokens
    ADD CONSTRAINT pk_execution_tokens PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.executions
    ADD CONSTRAINT pk_executions PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.instances
    ADD CONSTRAINT pk_instances PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.messages
    ADD CONSTRAINT pk_messages PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.model_resources
    ADD CONSTRAINT pk_model_resources PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.nodes
    ADD CONSTRAINT pk_nodes PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.outcomes
    ADD CONSTRAINT pk_outcomes PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.policies
    ADD CONSTRAINT pk_policies PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.principals
    ADD CONSTRAINT pk_principals PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.records
    ADD CONSTRAINT pk_records PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.runtime_installations
    ADD CONSTRAINT pk_runtime_installations PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.runtime_types
    ADD CONSTRAINT pk_runtime_types PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.secrets
    ADD CONSTRAINT pk_secrets PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.service_accounts
    ADD CONSTRAINT pk_service_accounts PRIMARY KEY (principal_id)
    """,
    """
ALTER TABLE ONLY public.system_settings
    ADD CONSTRAINT pk_system_settings PRIMARY KEY (key)
    """,
    """
ALTER TABLE ONLY public.trace_spans
    ADD CONSTRAINT pk_trace_spans PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT pk_user_sessions PRIMARY KEY (id)
    """,
    """
ALTER TABLE ONLY public.users
    ADD CONSTRAINT pk_users PRIMARY KEY (principal_id)
    """,
    """
ALTER TABLE ONLY public.agent_revisions
    ADD CONSTRAINT uq_agent_revisions_agent_id_revision_no UNIQUE (agent_id, revision_no)
    """,
    """
ALTER TABLE ONLY public.agents
    ADD CONSTRAINT uq_agents_name UNIQUE (name)
    """,
    """
ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT uq_api_keys_token_hash UNIQUE (token_hash)
    """,
    """
ALTER TABLE ONLY public.cluster_join_tokens
    ADD CONSTRAINT uq_cluster_join_tokens_token_hash UNIQUE (token_hash)
    """,
    """
ALTER TABLE ONLY public.clusters
    ADD CONSTRAINT uq_clusters_name UNIQUE (name)
    """,
    """
ALTER TABLE ONLY public.data_assets
    ADD CONSTRAINT uq_data_assets_name UNIQUE (name)
    """,
    """
ALTER TABLE ONLY public.documents
    ADD CONSTRAINT uq_documents_asset_id_sha256 UNIQUE (asset_id, sha256)
    """,
    """
ALTER TABLE ONLY public.egress_flows
    ADD CONSTRAINT uq_egress_flows_hour_node_source_ip_domain UNIQUE (hour, node, source_ip, domain)
    """,
    """
ALTER TABLE ONLY public.execution_attempts
    ADD CONSTRAINT uq_execution_attempts_execution_id_attempt_no UNIQUE (execution_id, attempt_no)
    """,
    """
ALTER TABLE ONLY public.execution_tokens
    ADD CONSTRAINT uq_execution_tokens_token_hash UNIQUE (token_hash)
    """,
    """
ALTER TABLE ONLY public.model_resources
    ADD CONSTRAINT uq_model_resources_name UNIQUE (name)
    """,
    """
ALTER TABLE ONLY public.nodes
    ADD CONSTRAINT uq_nodes_name UNIQUE (name)
    """,
    """
ALTER TABLE ONLY public.nodes
    ADD CONSTRAINT uq_nodes_overlay_ip UNIQUE (overlay_ip)
    """,
    """
ALTER TABLE ONLY public.nodes
    ADD CONSTRAINT uq_nodes_token_hash UNIQUE (token_hash)
    """,
    """
ALTER TABLE ONLY public.policies
    ADD CONSTRAINT uq_policies_name UNIQUE (name)
    """,
    """
ALTER TABLE ONLY public.records
    ADD CONSTRAINT uq_records_asset_id UNIQUE (asset_id, key)
    """,
    """
ALTER TABLE ONLY public.runtime_types
    ADD CONSTRAINT uq_runtime_types_name UNIQUE (name)
    """,
    """
ALTER TABLE ONLY public.secrets
    ADD CONSTRAINT uq_secrets_name UNIQUE (name)
    """,
    """
ALTER TABLE ONLY public.service_accounts
    ADD CONSTRAINT uq_service_accounts_agent_id UNIQUE (agent_id)
    """,
    """
ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT uq_user_sessions_token_hash UNIQUE (token_hash)
    """,
    """
ALTER TABLE ONLY public.users
    ADD CONSTRAINT uq_users_login UNIQUE (login)
    """,
    """
CREATE INDEX ix_api_keys_user ON public.api_keys USING btree (user_id)
    """,
    """
CREATE INDEX ix_audit_events_decision ON public.audit_events USING btree (decision)
    """,
    """
CREATE INDEX ix_audit_events_execution_id ON public.audit_events USING btree (execution_id)
    """,
    """
CREATE INDEX ix_audit_events_occurred_at ON public.audit_events USING btree (occurred_at)
    """,
    """
CREATE INDEX ix_chunks_asset_class ON public.chunks USING btree (asset_id, data_class)
    """,
    """
CREATE INDEX ix_chunks_embedding_hnsw ON public.chunks USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64')
    """,
    """
CREATE INDEX ix_chunks_tsv ON public.chunks USING gin (tsv)
    """,
    """
CREATE INDEX ix_egress_flows_hour ON public.egress_flows USING btree (hour)
    """,
    """
CREATE INDEX ix_execution_tokens_execution ON public.execution_tokens USING btree (execution_id)
    """,
    """
CREATE INDEX ix_messages_conversation ON public.messages USING btree (conversation_id, created_at)
    """,
    """
CREATE INDEX ix_records_asset_label ON public.records USING btree (asset_id, label)
    """,
    """
CREATE INDEX ix_trace_spans_execution_started ON public.trace_spans USING btree (execution_id, started_at)
    """,
    """
CREATE INDEX ix_trace_spans_started_at ON public.trace_spans USING btree (started_at)
    """,
    """
CREATE UNIQUE INDEX uq_deployments_channel_hash ON public.deployments USING btree (channel_hash) WHERE (channel_hash IS NOT NULL)
    """,
    """
CREATE TRIGGER audit_events_immutable BEFORE DELETE OR UPDATE ON public.audit_events FOR EACH ROW EXECUTE FUNCTION public.audit_events_immutable()
    """,
    """
ALTER TABLE ONLY public.agent_revisions
    ADD CONSTRAINT fk_agent_revisions_agent_id_agents FOREIGN KEY (agent_id) REFERENCES public.agents(id)
    """,
    """
ALTER TABLE ONLY public.agents
    ADD CONSTRAINT fk_agents_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(principal_id)
    """,
    """
ALTER TABLE ONLY public.agents
    ADD CONSTRAINT fk_agents_runtime_type_id_runtime_types FOREIGN KEY (runtime_type_id) REFERENCES public.runtime_types(id)
    """,
    """
ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT fk_api_keys_agent_id_agents FOREIGN KEY (agent_id) REFERENCES public.agents(id)
    """,
    """
ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT fk_api_keys_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(principal_id)
    """,
    """
ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT fk_audit_events_execution_id_executions FOREIGN KEY (execution_id) REFERENCES public.executions(id)
    """,
    """
ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT fk_chunks_asset_id_data_assets FOREIGN KEY (asset_id) REFERENCES public.data_assets(id)
    """,
    """
ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT fk_chunks_document_id_documents FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE
    """,
    """
ALTER TABLE ONLY public.cluster_join_tokens
    ADD CONSTRAINT fk_cluster_join_tokens_node_id_nodes FOREIGN KEY (node_id) REFERENCES public.nodes(id)
    """,
    """
ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT fk_conversations_agent_id_agents FOREIGN KEY (agent_id) REFERENCES public.agents(id)
    """,
    """
ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT fk_conversations_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(principal_id)
    """,
    """
ALTER TABLE ONLY public.data_assets
    ADD CONSTRAINT fk_data_assets_owner_id_users FOREIGN KEY (owner_id) REFERENCES public.users(principal_id)
    """,
    """
ALTER TABLE ONLY public.deployments
    ADD CONSTRAINT fk_deployments_agent_revision_id_agent_revisions FOREIGN KEY (agent_revision_id) REFERENCES public.agent_revisions(id)
    """,
    """
ALTER TABLE ONLY public.deployments
    ADD CONSTRAINT fk_deployments_cluster_id_clusters FOREIGN KEY (cluster_id) REFERENCES public.clusters(id)
    """,
    """
ALTER TABLE ONLY public.deployments
    ADD CONSTRAINT fk_deployments_node_id_nodes FOREIGN KEY (node_id) REFERENCES public.nodes(id)
    """,
    """
ALTER TABLE ONLY public.documents
    ADD CONSTRAINT fk_documents_asset_id_data_assets FOREIGN KEY (asset_id) REFERENCES public.data_assets(id)
    """,
    """
ALTER TABLE ONLY public.documents
    ADD CONSTRAINT fk_documents_uploaded_by_users FOREIGN KEY (uploaded_by) REFERENCES public.users(principal_id)
    """,
    """
ALTER TABLE ONLY public.execution_attempts
    ADD CONSTRAINT fk_execution_attempts_execution_id_executions FOREIGN KEY (execution_id) REFERENCES public.executions(id)
    """,
    """
ALTER TABLE ONLY public.execution_attempts
    ADD CONSTRAINT fk_execution_attempts_instance_id_instances FOREIGN KEY (instance_id) REFERENCES public.instances(id)
    """,
    """
ALTER TABLE ONLY public.execution_attempts
    ADD CONSTRAINT fk_execution_attempts_model_resource_id_model_resources FOREIGN KEY (model_resource_id) REFERENCES public.model_resources(id)
    """,
    """
ALTER TABLE ONLY public.execution_attempts
    ADD CONSTRAINT fk_execution_attempts_runtime_installation_id_runtime_i_efe4 FOREIGN KEY (runtime_installation_id) REFERENCES public.runtime_installations(id)
    """,
    """
ALTER TABLE ONLY public.execution_tokens
    ADD CONSTRAINT fk_execution_tokens_execution_id_executions FOREIGN KEY (execution_id) REFERENCES public.executions(id)
    """,
    """
ALTER TABLE ONLY public.executions
    ADD CONSTRAINT fk_executions_agent_revision_id_agent_revisions FOREIGN KEY (agent_revision_id) REFERENCES public.agent_revisions(id)
    """,
    """
ALTER TABLE ONLY public.executions
    ADD CONSTRAINT fk_executions_api_key_id_api_keys FOREIGN KEY (api_key_id) REFERENCES public.api_keys(id)
    """,
    """
ALTER TABLE ONLY public.executions
    ADD CONSTRAINT fk_executions_deployment_id_deployments FOREIGN KEY (deployment_id) REFERENCES public.deployments(id)
    """,
    """
ALTER TABLE ONLY public.executions
    ADD CONSTRAINT fk_executions_effective_principal_id_principals FOREIGN KEY (effective_principal_id) REFERENCES public.principals(id)
    """,
    """
ALTER TABLE ONLY public.executions
    ADD CONSTRAINT fk_executions_initiated_by_principal_id_principals FOREIGN KEY (initiated_by_principal_id) REFERENCES public.principals(id)
    """,
    """
ALTER TABLE ONLY public.executions
    ADD CONSTRAINT fk_executions_parent_execution_id_executions FOREIGN KEY (parent_execution_id) REFERENCES public.executions(id)
    """,
    """
ALTER TABLE ONLY public.instances
    ADD CONSTRAINT fk_instances_deployment_id_deployments FOREIGN KEY (deployment_id) REFERENCES public.deployments(id)
    """,
    """
ALTER TABLE ONLY public.instances
    ADD CONSTRAINT fk_instances_node_id_nodes FOREIGN KEY (node_id) REFERENCES public.nodes(id)
    """,
    """
ALTER TABLE ONLY public.instances
    ADD CONSTRAINT fk_instances_runtime_installation_id_runtime_installations FOREIGN KEY (runtime_installation_id) REFERENCES public.runtime_installations(id)
    """,
    """
ALTER TABLE ONLY public.messages
    ADD CONSTRAINT fk_messages_conversation_id_conversations FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE
    """,
    """
ALTER TABLE ONLY public.messages
    ADD CONSTRAINT fk_messages_execution_id_executions FOREIGN KEY (execution_id) REFERENCES public.executions(id)
    """,
    """
ALTER TABLE ONLY public.model_resources
    ADD CONSTRAINT fk_model_resources_node_id_nodes FOREIGN KEY (node_id) REFERENCES public.nodes(id)
    """,
    """
ALTER TABLE ONLY public.nodes
    ADD CONSTRAINT fk_nodes_cluster_id_clusters FOREIGN KEY (cluster_id) REFERENCES public.clusters(id)
    """,
    """
ALTER TABLE ONLY public.outcomes
    ADD CONSTRAINT fk_outcomes_confirmed_by_users FOREIGN KEY (confirmed_by) REFERENCES public.users(principal_id)
    """,
    """
ALTER TABLE ONLY public.outcomes
    ADD CONSTRAINT fk_outcomes_execution_id_executions FOREIGN KEY (execution_id) REFERENCES public.executions(id)
    """,
    """
ALTER TABLE ONLY public.outcomes
    ADD CONSTRAINT fk_outcomes_rated_by_users FOREIGN KEY (rated_by) REFERENCES public.users(principal_id)
    """,
    """
ALTER TABLE ONLY public.records
    ADD CONSTRAINT fk_records_asset_id_data_assets FOREIGN KEY (asset_id) REFERENCES public.data_assets(id)
    """,
    """
ALTER TABLE ONLY public.runtime_installations
    ADD CONSTRAINT fk_runtime_installations_node_id_nodes FOREIGN KEY (node_id) REFERENCES public.nodes(id)
    """,
    """
ALTER TABLE ONLY public.runtime_installations
    ADD CONSTRAINT fk_runtime_installations_runtime_type_id_runtime_types FOREIGN KEY (runtime_type_id) REFERENCES public.runtime_types(id)
    """,
    """
ALTER TABLE ONLY public.service_accounts
    ADD CONSTRAINT fk_service_accounts_agent_id_agents FOREIGN KEY (agent_id) REFERENCES public.agents(id)
    """,
    """
ALTER TABLE ONLY public.service_accounts
    ADD CONSTRAINT fk_service_accounts_owner_user_id_users FOREIGN KEY (owner_user_id) REFERENCES public.users(principal_id)
    """,
    """
ALTER TABLE ONLY public.service_accounts
    ADD CONSTRAINT fk_service_accounts_principal_id_principals FOREIGN KEY (principal_id) REFERENCES public.principals(id)
    """,
    """
ALTER TABLE ONLY public.trace_spans
    ADD CONSTRAINT fk_trace_spans_agent_id_agents FOREIGN KEY (agent_id) REFERENCES public.agents(id)
    """,
    """
ALTER TABLE ONLY public.trace_spans
    ADD CONSTRAINT fk_trace_spans_attempt_id_execution_attempts FOREIGN KEY (attempt_id) REFERENCES public.execution_attempts(id)
    """,
    """
ALTER TABLE ONLY public.trace_spans
    ADD CONSTRAINT fk_trace_spans_execution_id_executions FOREIGN KEY (execution_id) REFERENCES public.executions(id)
    """,
    """
ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT fk_user_sessions_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(principal_id)
    """,
    """
ALTER TABLE ONLY public.users
    ADD CONSTRAINT fk_users_manager_id_users FOREIGN KEY (manager_id) REFERENCES public.users(principal_id)
    """,
    """
ALTER TABLE ONLY public.users
    ADD CONSTRAINT fk_users_principal_id_principals FOREIGN KEY (principal_id) REFERENCES public.principals(id)
    """,
]

TABLES = [
    "agent_revisions",
    "agents",
    "api_keys",
    "audit_events",
    "chunks",
    "cluster_join_tokens",
    "clusters",
    "conversations",
    "data_assets",
    "deployments",
    "documents",
    "egress_flows",
    "execution_attempts",
    "execution_tokens",
    "executions",
    "instances",
    "messages",
    "model_resources",
    "nodes",
    "outcomes",
    "policies",
    "principals",
    "records",
    "runtime_installations",
    "runtime_types",
    "secrets",
    "service_accounts",
    "system_settings",
    "trace_spans",
    "user_sessions",
    "users",
]
TYPES = []
FUNCTIONS = ["audit_events_immutable()"]
SEQUENCES = []

RUNTIME_TYPES = [
    ("native", "Yorath agent loop"),
    ("codex", "Codex CLI"),
    ("openclaw", "OpenClaw"),
    ("hermes", "Hermes Agent"),
]


def upgrade() -> None:
    bind = op.get_bind()
    for statement in SCHEMA:
        bind.exec_driver_sql(statement)
    op.bulk_insert(
        sa.table("clusters", sa.column("id", sa.Uuid()), sa.column("name", sa.String())),
        [{"id": uuid.uuid7(), "name": "local"}],
    )
    op.bulk_insert(
        sa.table(
            "runtime_types",
            sa.column("id", sa.Uuid()),
            sa.column("name", sa.String()),
            sa.column("display_name", sa.String()),
        ),
        [{"id": uuid.uuid7(), "name": n, "display_name": d} for n, d in RUNTIME_TYPES],
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        bind.exec_driver_sql(f"DROP TABLE IF EXISTS public.{table} CASCADE")
    for sequence in SEQUENCES:
        bind.exec_driver_sql(f"DROP SEQUENCE IF EXISTS public.{sequence} CASCADE")
    for function in FUNCTIONS:
        bind.exec_driver_sql(f"DROP FUNCTION IF EXISTS public.{function} CASCADE")
    for type_ in TYPES:
        bind.exec_driver_sql(f"DROP TYPE IF EXISTS public.{type_} CASCADE")
    bind.exec_driver_sql("DROP EXTENSION IF EXISTS vector")
