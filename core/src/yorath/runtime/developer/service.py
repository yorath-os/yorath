"""Running the Developer Agent. GitHub decides the result, not the model text (contract rule 3)."""

import contextlib
import logging
import os
import re
import shlex
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yorath import PRODUCT_NAME
from yorath.control.journal import AuditEventType, ControlLevel, Decision, Journal, SpanKind
from yorath.control.model import (
    DataClass,
    ExecutionStatus,
    ExecutionTrigger,
    Outcome,
    OutcomeKind,
    ServiceAccount,
)
from yorath.enforcement.gateway import ExecutionContext, finish, load_agent, open_context
from yorath.enforcement.gateway.context import ContextRegistry
from yorath.enforcement.gateway.errors import GatewayError
from yorath.enforcement.gateway.tokens import issue_execution_token
from yorath.enforcement.sandbox.client import SandboxHost
from yorath.enforcement.sandbox.egress import summarize
from yorath.runtime.adapters import CodexAdapter, ExecutionSpec, PreparedRun, RunResult
from yorath.runtime.adapters.contract import RuntimeEvent
from yorath.runtime.developer.github import GitHub, GitHubConfig, GitHubError

log = logging.getLogger(__name__)

AGENT_NAME = "developer-agent"
TRIGGER_LABEL = "yorath"
SANDBOX_UID = 1000  # the node user in the sandbox image
COMMAND_LIMIT = 500
SCRIPT_ERRORS = {  # run.sh exit codes: stderr here is Codex noise, not the cause
    3: "could not fetch the repository",
    4: "could not push the branch",
}
STATUS = {
    "ok": ExecutionStatus.SUCCEEDED,
    "failed": ExecutionStatus.FAILED,
    "timeout": ExecutionStatus.TIMEOUT,
    "config_error": ExecutionStatus.CONFIG_ERROR,
}


@dataclass(frozen=True)
class Issue:
    repository: str
    number: int
    title: str
    body: str
    default_branch: str

    @property
    def branch(self) -> str:
        return f"yorath/issue-{self.number}"


@dataclass
class DeveloperDeps:
    sessions: async_sessionmaker[AsyncSession]
    journal: Journal
    contexts: ContextRegistry
    http: httpx.AsyncClient
    agents_dir: Path
    sandbox_dir: Path
    sandbox_url: str
    sandbox_token: str
    gateway_url: str
    github_url: str
    egress_log: Path
    token_ttl_minutes: int
    codex_sandbox: str = "danger-full-access"
    timeout_s: float = 1800


def render_script(prepared: PreparedRun, issue: Issue, git_token_path: Path) -> str:
    """Clone, Codex, commit and push in the sandbox. The URL has no token; ``GIT_ASKPASS`` reads
    it."""
    run_dir = prepared.spec.run_dir
    workspace = shlex.quote(str(prepared.spec.workspace))
    remote = shlex.quote(f"https://x-access-token@github.com/{issue.repository}.git")
    branch = shlex.quote(issue.branch)
    askpass = shlex.quote(str(run_dir / "askpass.sh"))
    message = shlex.quote(f"{PRODUCT_NAME}: issue #{issue.number}")
    return f"""#!/bin/sh
printf '#!/bin/sh\\ncat %s\\n' {shlex.quote(str(git_token_path))} > {askpass}
chmod 700 {askpass}
export GIT_ASKPASS={askpass} GIT_TERMINAL_PROMPT=0
git clone -q --depth 50 {remote} {workspace} >&2 || exit 3
git -C {workspace} checkout -q -b {branch} >&2 || exit 3
{shlex.join(prepared.command)}
code=$?
[ "$code" = 0 ] || exit "$code"
git -C {workspace} add -A >&2
git -C {workspace} -c user.name='{PRODUCT_NAME} Developer Agent' -c user.email=yorath@localhost \\
  commit -q -m {message} >&2 || exit 0
git -C {workspace} push -q origin {branch} >&2 || exit 4
"""


def _hand_over(run_dir: Path) -> None:
    """The sandbox does not run as root: the run directory is given to its user."""
    for path in [run_dir, *run_dir.rglob("*")]:
        with contextlib.suppress(PermissionError):
            os.chown(path, SANDBOX_UID, SANDBOX_UID)


async def _audit_egress(deps: DeveloperDeps, ctx: ExecutionContext, ip: str | None, since: float):
    if not ip or not deps.egress_log.is_file():
        return
    lines = deps.egress_log.read_text(encoding="utf-8", errors="replace").splitlines()
    for (domain, allowed), count in summarize(lines, ip, since).items():
        await deps.journal.audit(
            AuditEventType.EGRESS_ALLOWED if allowed else AuditEventType.EGRESS_BLOCKED,
            **ctx.audit_fields(),
            decision=Decision.PERMIT if allowed else Decision.DENY,
            details={"domain": domain, "count": count},
        )


async def _record_event(deps: DeveloperDeps, ctx: ExecutionContext, event: RuntimeEvent) -> None:
    """Trace without ``raw``: command output could contain the token from the run directory."""
    if event.kind not in {"tool.called", "llm.called", "runtime.warning"}:
        return
    data: dict[str, Any] = {k: v for k, v in event.data.items() if v is not None}
    if "command" in data:
        data["command"] = str(data["command"])[:COMMAND_LIMIT]
    if "paths" in data:
        workspace = f"{deps.sandbox_dir / str(ctx.execution_id) / 'ws'}/"
        data["paths"] = [p.removeprefix(workspace) for p in data["paths"]]
    # the runtime's own words, not a checked fact: cost and decisions come only from Gateway audit
    data["yorath.source"] = "runtime"
    async with deps.journal.span(
        ctx.execution_id,
        event.kind,
        SpanKind.RUNTIME,
        attempt_id=ctx.attempt.id,
        agent_id=ctx.agent.id,
        attributes=data,
    ):
        pass


CONVENTIONAL = re.compile(
    r"^(feat|fix|docs|refactor|test|chore|ci|perf|build)(\([^)]+\))?!?: \S.{0,99}$"
)


def pull_title(summary: str, issue_title: str) -> tuple[str, str]:
    """PR title in Conventional Commits format, and the description without it."""
    first, _, rest = summary.strip().partition("\n")
    first = first.strip().strip("`")
    if CONVENTIONAL.match(first):
        return first, rest.strip()
    return f"chore: {issue_title.strip()}"[:120], summary.strip()


async def _publish(
    deps: DeveloperDeps,
    db: AsyncSession,
    ctx: ExecutionContext,
    github: GitHub,
    token: str,
    issue: Issue,
    result: RunResult,
) -> None:
    commits = await github.ahead_commits(
        token, issue.repository, issue.default_branch, issue.branch
    )
    if not commits:
        await github.comment(
            token,
            issue.repository,
            issue.number,
            "The agent finished, but the branch has no changes.",
        )
        return
    title, summary = pull_title(result.summary, issue.title)
    body = f"{summary}\n\nCloses #{issue.number}\n\nExecution: `{ctx.execution_id}`"
    pull = await github.open_pull_request(
        token, issue.repository, issue.default_branch, issue.branch, title, body
    )
    url = str(pull.get("html_url", ""))
    db.add(
        Outcome(
            execution_id=ctx.execution_id,
            kind=OutcomeKind.PULL_REQUEST,
            ref=url,
            data={
                "kind": "pull_request",
                "repository": issue.repository,
                "branch": issue.branch,
                "commits": commits,
                "pr_number": pull.get("number"),
            },
        )
    )
    await db.commit()
    await deps.journal.audit(
        AuditEventType.OUTCOME_RECORDED,
        **ctx.audit_fields(),
        details={"kind": "pull_request", "ref": url},
    )
    await github.comment(token, issue.repository, issue.number, f"Opened pull request: {url}")


async def _sandbox_run(
    deps: DeveloperDeps, ctx: ExecutionContext, issue: Issue, git_token: str
) -> RunResult:
    run_dir = deps.sandbox_dir / str(ctx.execution_id)
    run_dir.mkdir(parents=True)
    git_token_path = run_dir / "git-token"
    git_token_path.touch(mode=0o600)
    git_token_path.write_text(git_token, encoding="utf-8")
    async with deps.sessions() as db:
        execution_token = await issue_execution_token(
            db, deps.journal, ctx.audit_fields(), deps.token_ttl_minutes
        )
    spec = ExecutionSpec(
        execution_id=str(ctx.execution_id),
        instructions=ctx.body,
        task=f"{issue.title}\n\n{issue.body}",
        model=ctx.card.model,
        gateway_url=deps.gateway_url,
        execution_token=execution_token,
        workspace=run_dir / "ws",
        run_dir=run_dir,
        timeout_s=deps.timeout_s,
        codex_sandbox=deps.codex_sandbox,
    )
    adapter = CodexAdapter()
    prepared = adapter.prepare(spec)
    (run_dir / "run.sh").write_text(
        render_script(prepared, issue, git_token_path), encoding="utf-8"
    )
    _hand_over(run_dir)
    prepared.command = ["sh", str(run_dir / "run.sh")]

    async def notify(kind: str, data: dict) -> None:
        event = (
            AuditEventType.SANDBOX_STARTED if kind == "started" else AuditEventType.SANDBOX_STOPPED
        )
        await deps.journal.audit(event, **ctx.audit_fields(), details=data)

    host = SandboxHost(
        deps.http, deps.sandbox_url, deps.sandbox_token, ctx.execution_id, run_dir, notify
    )
    result = RunResult(status="failed")
    since = time.time()
    try:
        async for event in adapter.run(prepared, host, result):
            await _record_event(deps, ctx, event)
        if prepared.output_file.is_file():
            text = prepared.output_file.read_text(encoding="utf-8").strip()
            result.summary = text or result.summary
    finally:
        await _audit_egress(deps, ctx, host.ip, since)
        adapter.cleanup(prepared)
    return result


async def _open(deps: DeveloperDeps, db: AsyncSession) -> ExecutionContext:
    agent, rev, card, body = await load_agent(db, AGENT_NAME, deps.agents_dir)
    account = await db.scalar(select(ServiceAccount).where(ServiceAccount.agent_id == agent.id))
    if account is None:
        raise GatewayError("agent_not_found", "the agent has no service account")
    ctx = await open_context(
        db, deps.journal, agent=agent, revision=rev, card=card, body=body, actor=account,
        effective_principal=account, trigger=ExecutionTrigger.EVENT,
        control_level=ControlLevel.SANDBOX,
    )  # fmt: skip
    ctx.max_data_class_seen = DataClass.INTERNAL  # repository code is not classified
    deps.contexts.add(ctx)
    return ctx


async def run_issue(deps: DeveloperDeps, config: GitHubConfig, issue: Issue) -> uuid.UUID | None:
    """The full path of one issue. The token is revoked and the Execution closed on any outcome."""
    async with deps.sessions() as db:
        try:
            ctx = await _open(deps, db)
        except GatewayError as exc:
            log.warning("run of %s rejected: %s", AGENT_NAME, exc)
            return None
    github = GitHub(deps.http, deps.github_url, config)
    token: str | None = None
    status, error = ExecutionStatus.FAILED, None
    try:
        token = await github.repo_token(issue.repository)
        await deps.journal.audit(
            AuditEventType.SECRET_ISSUED,
            **ctx.audit_fields(),
            details={"kind": "github_token", "repository": issue.repository},
        )
        result = await _sandbox_run(deps, ctx, issue, token)
        status = STATUS[result.status]
        error = SCRIPT_ERRORS.get(result.exit_code or 0, result.error_message)
        async with deps.sessions() as db:
            if result.status == "ok":
                await _publish(deps, db, ctx, github, token, issue, result)
            else:
                await github.comment(
                    token,
                    issue.repository,
                    issue.number,
                    f"The agent did not manage it: {error or result.status}.",
                )
    except (GitHubError, httpx.HTTPError, OSError) as exc:
        status, error = ExecutionStatus.FAILED, str(exc)[:500]
    finally:
        if token is not None:
            revoked = True
            try:
                await github.revoke(token)
            except GitHubError, httpx.HTTPError:
                # the token expires by itself in an hour, but the journal must tell the truth
                revoked = False
            await deps.journal.audit(
                AuditEventType.SECRET_REVOKED,
                **ctx.audit_fields(),
                details={"kind": "github_token", "revoked": revoked},
            )
        async with deps.sessions() as db:
            await finish(ctx, db, deps.journal, status, error=error)
        deps.contexts.remove(ctx.execution_id)
    return ctx.execution_id
