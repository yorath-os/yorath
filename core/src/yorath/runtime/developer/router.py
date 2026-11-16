"""GitHub App and webhook setup. A person with rights sets the label: someone else's issue does not
run code."""

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from yorath.control.identity.deps import JournalDep, Owner
from yorath.control.journal import AuditEventType
from yorath.runtime.developer.github import (
    SECRET_NAME,
    GitHubConfig,
    load_config,
    valid_signature,
)
from yorath.runtime.developer.service import TRIGGER_LABEL, DeveloperDeps, Issue, run_issue

router = APIRouter(prefix="/github", tags=["github"])


class GitHubStatus(BaseModel):
    configured: bool
    app_id: str | None = None
    installation_id: str | None = None
    repositories: list[str] = []


@router.get("/app", response_model=GitHubStatus)
async def app_status(_: Owner, request: Request):
    config = await load_config(request.app.state.secrets)
    if config is None:
        return GitHubStatus(configured=False)
    return GitHubStatus(
        configured=True,
        app_id=config.app_id,
        installation_id=config.installation_id,
        repositories=config.repositories,
    )


@router.put("/app", response_model=GitHubStatus)
async def app_configure(data: GitHubConfig, owner: Owner, journal: JournalDep, request: Request):
    await request.app.state.secrets.put(SECRET_NAME, data.model_dump_json())
    await journal.audit(
        AuditEventType.SECRET_ISSUED,
        actor_principal_id=owner.id,
        details={"kind": "github_app", "repositories": data.repositories},
    )
    return await app_status(owner, request)


def deps_from(request: Request) -> DeveloperDeps:
    state, settings = request.app.state, request.app.state.settings
    return DeveloperDeps(
        sessions=state.sessions,
        journal=state.journal,
        contexts=state.contexts,
        http=state.http,
        agents_dir=settings.agents_dir,
        sandbox_dir=settings.sandbox_dir,
        sandbox_url=settings.sandbox_url,
        sandbox_token=settings.sandbox_token,
        gateway_url=settings.sandbox_gateway_url,
        github_url=settings.github_api_url,
        egress_log=Path(settings.egress_log),
        token_ttl_minutes=state.manifest.gateway.execution_token_ttl_minutes,
        codex_sandbox=settings.codex_sandbox,
    )


def parse_issue(payload: dict, allowed: list[str]) -> Issue | None:
    if payload.get("action") != "labeled":
        return None
    if (payload.get("label") or {}).get("name") != TRIGGER_LABEL:
        return None
    repo = payload.get("repository") or {}
    issue = payload.get("issue") or {}
    name = str(repo.get("full_name", ""))
    if name not in allowed or "pull_request" in issue:
        return None
    return Issue(
        repository=name,
        number=int(issue["number"]),
        title=str(issue.get("title", "")),
        body=str(issue.get("body") or ""),
        default_branch=str(repo.get("default_branch", "main")),
    )


@router.post("/webhook", status_code=202)
async def webhook(
    request: Request,
    signature: Annotated[str, Header(alias="X-Hub-Signature-256")] = "",
    event: Annotated[str, Header(alias="X-GitHub-Event")] = "",
    delivery: Annotated[str, Header(alias="X-GitHub-Delivery")] = "",
):
    state = request.app.state
    config = await load_config(state.secrets)
    if config is None:
        raise HTTPException(503, detail="github_not_configured")
    body = await request.body()
    if not valid_signature(config.webhook_secret, body, signature):
        raise HTTPException(401, detail="bad_signature")
    issue = parse_issue(await request.json(), config.repositories) if event == "issues" else None
    if issue is None or delivery in state.github_deliveries:
        return {"accepted": False}
    key = (issue.repository, issue.number)
    if key in state.github_running:
        return {"accepted": False}
    state.github_deliveries.add(delivery)
    state.github_running.add(key)

    async def work() -> None:
        try:
            if await run_issue(deps_from(request), config, issue) is None:
                # Execution not opened (agent disabled, no account): a redelivery will work
                state.github_deliveries.discard(delivery)
        finally:
            state.github_running.discard(key)

    task = asyncio.create_task(work())
    state.background.add(task)
    task.add_done_callback(state.background.discard)
    return {"accepted": True}
