"""Model invariants on a live database: migration, delegation chain, service account, Outcome."""

from decimal import Decimal

import pytest
from sqlalchemy import inspect, select, text

from tests.fixtures.world import make_agent, make_user
from yorath.control.model import (
    AgentMode,
    AttemptStatus,
    Cluster,
    DataClass,
    Deployment,
    Execution,
    ExecutionAttempt,
    ExecutionStatus,
    ExecutionTrigger,
    Outcome,
    OutcomeKind,
    OutcomeStatus,
    Principal,
    PrincipalKind,
    PullRequestOutcome,
    RuntimeType,
    ServiceAccount,
    User,
)

pytestmark = pytest.mark.group

EXPECTED_TABLES = {
    "principals", "users", "service_accounts", "clusters", "nodes", "runtime_types",
    "runtime_installations", "model_resources", "data_assets", "policies", "agents",
    "agent_revisions", "deployments", "instances", "executions", "execution_attempts",
    "outcomes", "trace_spans", "audit_events",
}  # fmt: skip


async def test_migration_created_tables_and_reference_rows(sessions):
    async with sessions() as s:
        conn = await s.connection()
        names = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
        assert names >= EXPECTED_TABLES
        assert (await s.scalars(select(Cluster.name))).all() == ["local"]
        assert set((await s.scalars(select(RuntimeType.name))).all()) == {
            "native",
            "codex",
            "openclaw",
            "hermes",
        }


@pytest.mark.covers("INV-AGT-2")
async def test_delegation_chain_keeps_effective_principal(session):
    anna = await make_user(session, "anna", role="hr", department="HR")
    da = await make_agent(session, "document-assistant", AgentMode.DELEGATED)
    research = await make_agent(session, "research-agent", AgentMode.DELEGATED)

    e1 = Execution(
        agent_revision_id=da.id,
        initiated_by_principal_id=anna.id,
        effective_principal_id=anna.id,
        trigger=ExecutionTrigger.USER_MESSAGE,
        status=ExecutionStatus.RUNNING,
        max_data_class_seen=DataClass.CONFIDENTIAL,
    )
    session.add(e1)
    await session.flush()
    e2 = Execution(
        agent_revision_id=research.id,
        initiated_by_principal_id=anna.id,
        effective_principal_id=anna.id,
        parent_execution_id=e1.id,
        trigger=ExecutionTrigger.AGENT_CALL,
        status=ExecutionStatus.DENIED,
        max_data_class_seen=e1.max_data_class_seen,
    )
    session.add(e2)
    await session.commit()

    chain = (
        await session.scalars(
            select(Execution)
            .where(Execution.effective_principal_id == anna.id)
            .order_by(Execution.created_at)
        )
    ).all()
    assert [e.agent_revision_id for e in chain] == [da.id, research.id]
    assert chain[1].parent_execution_id == e1.id and chain[1].status is ExecutionStatus.DENIED
    assert chain[1].max_data_class_seen is DataClass.CONFIDENTIAL
    # the Principal is exactly the user, loaded polymorphically
    principal = await session.get(Principal, anna.id)
    assert isinstance(principal, User) and principal.kind is PrincipalKind.USER


async def test_autonomous_execution_runs_as_service_account(session):
    owner = await make_user(session, "alex", role="owner")
    pagey = await make_agent(session, "pagey", AgentMode.AUTONOMOUS, runtime="codex")
    svc = ServiceAccount(display_name="pagey-svc", owner_user_id=owner.id, agent_id=pagey.agent_id)
    session.add(svc)
    await session.flush()

    cluster_id = await session.scalar(select(Cluster.id))
    deployment = Deployment(agent_revision_id=pagey.id, cluster_id=cluster_id)
    session.add(deployment)
    await session.flush()

    e3 = Execution(
        agent_revision_id=pagey.id,
        deployment_id=deployment.id,
        initiated_by_principal_id=svc.id,
        effective_principal_id=svc.id,
        trigger=ExecutionTrigger.EVENT,
        status=ExecutionStatus.SUCCEEDED,
    )
    session.add(e3)
    await session.flush()
    attempt = ExecutionAttempt(
        execution_id=e3.id,
        attempt_no=1,
        model="qwen3.5:9b",
        runtime_session_id="thread_abc",
        status=AttemptStatus.SUCCEEDED,
        usage={"input_tokens": 1000, "output_tokens": 200},
        cost_usd=Decimal("0.0012"),
        cost_estimated=True,
    )
    outcome = Outcome(
        execution_id=e3.id,
        kind=OutcomeKind.PULL_REQUEST,
        ref="https://github.com/example-org/pagey/pull/1",
        data=PullRequestOutcome(
            repository="example-org/pagey", branch="yorath/issue-1"
        ).model_dump(),
    )
    session.add_all([attempt, outcome])
    await session.commit()

    principal = await session.get(Principal, svc.id)
    assert isinstance(principal, ServiceAccount) and principal.kind is PrincipalKind.SERVICE
    assert principal.owner_user_id == owner.id and principal.agent_id == pagey.agent_id
    # the agent owner does not appear in the Execution
    assert e3.effective_principal_id != owner.id and e3.initiated_by_principal_id != owner.id
    loaded = await session.get(Execution, e3.id)
    await session.refresh(loaded, ["attempts", "outcomes"])
    assert len(loaded.attempts) == 1 and loaded.attempts[0].instance_id is None
    assert loaded.outcomes[0].status is OutcomeStatus.PENDING
    assert loaded.outcomes[0].data["kind"] == "pull_request"


async def test_schema_allows_second_attempt_and_forbids_duplicate_no(session):
    anna = await make_user(session, "anna2")
    da = await make_agent(session, "da2", AgentMode.DELEGATED)
    e = Execution(
        agent_revision_id=da.id,
        initiated_by_principal_id=anna.id,
        effective_principal_id=anna.id,
        trigger=ExecutionTrigger.USER_MESSAGE,
    )
    session.add(e)
    await session.flush()
    session.add_all(
        [
            ExecutionAttempt(execution_id=e.id, attempt_no=1, status=AttemptStatus.TIMEOUT),
            ExecutionAttempt(execution_id=e.id, attempt_no=2, status=AttemptStatus.SUCCEEDED),
        ]
    )
    await session.commit()
    session.add(ExecutionAttempt(execution_id=e.id, attempt_no=2, status=AttemptStatus.RUNNING))
    with pytest.raises(Exception, match="uq_execution_attempts_execution_id_attempt_no"):
        await session.commit()
    await session.rollback()


async def test_status_check_constraint(session):
    with pytest.raises(Exception, match="ck_executions_execution_status"):
        await session.execute(
            text(
                "insert into executions (id, agent_revision_id, initiated_by_principal_id, "
                "effective_principal_id, trigger, status) "
                "values (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), "
                "gen_random_uuid(), 'event', 'bogus')"
            )
        )
    await session.rollback()
