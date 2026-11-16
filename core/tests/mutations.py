"""Mutation check of the main invariants (R10): break it, watch the test fail, roll back.

    cd core && YORATH_DATABASE_URL=... uv run python -m tests.mutations

Each mutation changes one line of production code and runs the declared module. A surviving
mutation means the test guards nothing. The result is appended to tests/invariants.md.
"""

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.registry import INVARIANTS_DOC

CORE = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    invariant: str
    file: str
    old: str
    new: str
    what: str
    target: str


MUTATIONS = [
    Mutation(
        "INV-CLS-1",
        "src/yorath/runtime/rag/search.py",
        "allowed_ids = [d.asset_id for d in decisions if d.allowed]",
        "allowed_ids = [d.asset_id for d in decisions]",
        "search does not filter sources by the PDP decision",
        "tests/groups/test_rag.py",
    ),
    Mutation(
        "INV-CLS-3",
        "src/yorath/runtime/chat/service.py",
        "        ctx.raise_class(data_class)\n",
        "        pass\n",
        "a new Execution does not inherit the conversation class",
        "tests/groups/test_chat.py",
    ),
    Mutation(
        "INV-MOD-1",
        "src/yorath/enforcement/gateway/models.py",
        '        if not decision.allowed:\n            raise GatewayDenied(\n                "model_denied"',
        '        if False:\n            raise GatewayDenied(\n                "model_denied"',
        "a PDP deny on call_model is ignored",
        "tests/groups/test_gateway_models.py",
    ),
    Mutation(
        "INV-TOOL-1",
        "src/yorath/enforcement/gateway/tools.py",
        "        if not decision.allowed:\n",
        "        if False:\n",
        "a PDP deny on use_tool is ignored",
        "tests/groups/test_gateway_tools.py",
    ),
    Mutation(
        "INV-TOOL-2",
        "src/yorath/enforcement/policies/agents.cedar",
        "when { context.context_class > resource.max_class };",
        "when { context.context_class > 99 };",
        "the tool-class-ceiling policy does not fire",
        "tests/groups/test_web_search.py",
    ),
    Mutation(
        "INV-REC-1",
        "src/yorath/runtime/tools/query_records.py",
        "        if decision.allowed:\n            allowed.append(row)\n",
        "        if True:\n            allowed.append(row)\n",
        "a per-row PDP decision is ignored",
        "tests/groups/test_query_records.py",
    ),
    Mutation(
        "INV-AGT-4",
        "src/yorath/enforcement/gateway/context.py",
        "    if not agent.enabled and not allow_disabled:\n",
        "    if False:\n",
        "a disabled agent runs",
        "tests/groups/test_agents_api.py",
    ),
    Mutation(
        "INV-DEV-1",
        "src/yorath/runtime/developer/github.py",
        "    return hmac.compare_digest(expected, header)\n",
        "    return True\n",
        "an invalid signature is accepted",
        "tests/groups/test_developer_agent.py",
    ),
    Mutation(
        "INV-DEV-2",
        "src/yorath/runtime/developer/service.py",
        "    if not commits:\n",
        "    if False:\n",
        "a PR is created without commits",
        "tests/groups/test_developer_agent.py",
    ),
    Mutation(
        "INV-MOD-8",
        "src/yorath/enforcement/gateway/models.py",
        "        except GatewayError as exc:",
        "        except ZeroDivisionError as exc:",
        "an interrupted model call is not written to the audit log",
        "tests/groups/test_gateway_models.py",
    ),
    Mutation(
        "INV-DEV-4",
        "src/yorath/runtime/developer/router.py",
        "            if await run_issue(deps_from(request), config, issue) is None:\n",
        "            if await run_issue(deps_from(request), config, issue) or True:\n",
        "a redelivery opens a second Execution",
        "tests/groups/test_developer_agent.py",
    ),
    Mutation(
        "INV-SBX-1",
        "src/yorath/enforcement/sandbox/runner.py",
        "    env = {k: v for k, v in data.env.items() if k in ALLOWED_ENV}\n",
        "    env = dict(data.env)\n",
        "the request passes any environment to the container",
        "tests/unit/test_sandbox.py",
    ),
    Mutation(
        "INV-AGT-5",
        "src/yorath/control/agents/custom.py",
        "    if unknown := sorted(set(form.tools) - set(CUSTOM_TOOLS)):\n",
        "    if unknown := []:\n",
        "a custom agent gets any tool",
        "tests/groups/test_custom_agents.py",
    ),
    Mutation(
        "INV-AGT-1",
        "src/yorath/enforcement/gateway/agents.py",
        "            if not decision.allowed:\n",
        "            if False:\n",
        "a PDP deny on invoke_agent is ignored",
        "tests/groups/test_ask_agent.py",
    ),
    Mutation(
        "INV-EXEC-1",
        "src/yorath/runtime/chat/runs.py",
        "            self.queues.discard(queue)\n",
        "            self.queues.discard(queue)\n            if self.task:\n                self.task.cancel()\n",
        "a dropped subscription cancels the answer",
        "tests/groups/test_chat.py",
    ),
    Mutation(
        "INV-EXEC-1",
        "src/yorath/runtime/chat/service.py",
        "        if ctx is not None and not ctx.finished:\n",
        "        if False:\n",
        "a stopped answer does not close the Execution",
        "tests/groups/test_chat.py",
    ),
    Mutation(
        "INV-AUD-1",
        "alembic/versions/0001_initial.py",
        "BEFORE DELETE OR UPDATE ON public.audit_events",
        "BEFORE UPDATE ON public.audit_events",
        "the trigger does not forbid DELETE in the audit log",
        "tests/groups/test_journal.py",
    ),
    Mutation(
        "INV-ID-1",
        "src/yorath/control/identity/service.py",
        "if row is None or row.revoked_at is not None or row.expires_at < now:",
        "if row is None or row.expires_at < now:",
        "a revoked session is accepted",
        "tests/groups/test_identity.py",
    ),
    Mutation(
        "INV-ID-2",
        "src/yorath/control/identity/deps.py",
        "        if user.role not in roles:\n",
        "        if False:\n",
        "the role check is disabled",
        "tests/groups/test_agents_api.py",
    ),
    Mutation(
        "INV-GW-1",
        "src/yorath/enforcement/gateway/tokens.py",
        "if row is None or row.revoked_at is not None or row.expires_at <= utcnow():",
        "if row is None or row.revoked_at is not None:",
        "an expired Execution token is accepted",
        "tests/groups/test_gateway_http.py",
    ),
    Mutation(
        "INV-API-1",
        "src/yorath/enforcement/gateway/models.py",
        "[m.openai() for m in request.messages] if ctx.record_content else None",
        "[m.openai() for m in request.messages]",
        "the endpoint prompt is written to the trace",
        "tests/groups/test_model_endpoint.py",
    ),
    Mutation(
        "INV-KEY-1",
        "src/yorath/control/identity/api_keys.py",
        "if key is None or key.revoked_at is not None:",
        "if key is None:",
        "a revoked API key is accepted",
        "tests/groups/test_api_keys.py",
    ),
    Mutation(
        "INV-API-4",
        "src/yorath/enforcement/gateway/public.py",
        "    if key.agent_id is not None and key.scope != SELF:\n        raise EndpointError(",
        "    if False:\n        raise EndpointError(",
        "an agent key opens the bare model",
        "tests/groups/test_agent_api.py",
    ),
    Mutation(
        "INV-API-3",
        "src/yorath/enforcement/gateway/public.py",
        "if not finished and not seen_last and not settled:",
        "if not finished:",
        "a client leaving after the last chunk breaks accounting",
        "tests/groups/test_model_endpoint.py",
    ),
    Mutation(
        "INV-NET-1",
        "src/yorath/control/journal/egress.py",
        "if now is None or now == before:",
        "if True:",
        "removed network rules do not reach the audit log",
        "tests/groups/test_node_egress.py",
    ),
    Mutation(
        "INV-CON-1",
        "src/yorath/enforcement/gateway/public.py",
        "    if not agent.enabled:",
        "    if False:",
        "a disabled connected agent gets the model with its own key",
        "tests/groups/test_connected_agents.py",
    ),
    Mutation(
        "INV-CON-2",
        "src/yorath/runtime/connected/node_agent.py",
        'return spec is not None and image.startswith(spec.repository + ":")',
        "return True",
        "the node service runs an image from someone else's repository",
        "tests/unit/test_node_agent.py",
    ),
    Mutation(
        "INV-CLU-1",
        "src/yorath/control/agents/connected_router.py",
        'if node is None or node.status == "revoked":',
        "if node is None:",
        "a revoked node receives the desired state",
        "tests/groups/test_cluster.py",
    ),
    Mutation(
        "INV-CLU-2",
        "src/yorath/control/agents/connected.py",
        "    return deployment.node_id == node.id\n",
        "    return True\n",
        "a node receives and accepts other nodes' Deployments",
        "tests/groups/test_cluster.py",
    ),
    Mutation(
        "INV-PLC-3",
        "src/yorath/control/cluster/placements.py",
        'elif node.status != "active" or (node.role != "main" and not node.overlay_ip):',
        "elif False:",
        "a call goes to a placement on a lost node instead of being refused",
        "tests/groups/test_model_placements.py",
    ),
    Mutation(
        "INV-PLC-4",
        "src/yorath/control/cluster/placements.py",
        "missing = [c for c in classes if c not in node.allowed_data_classes]",
        "missing: list[str] = []",
        "a model is placed on a node that does not allow its data class",
        "tests/groups/test_model_placements.py",
    ),
    Mutation(
        "INV-QUE-1",
        "src/yorath/enforcement/gateway/slots.py",
        "return [c for c in queue.ready if self._busy[c.server_id] < c.slots]",
        "return list(queue.ready)",
        "a call is sent to a busy model server instead of waiting in the queue",
        "tests/groups/test_model_queue.py",
    ),
    Mutation(
        "INV-QUE-2",
        "src/yorath/enforcement/gateway/slots.py",
        "            self.ring.append(participant)",
        "            self.ring.appendleft(participant)",
        "one participant's burst is served before another participant's call",
        "tests/groups/test_model_queue.py",
    ),
]


def run(target: str) -> tuple[bool, str]:
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-x",
            "--no-header",
            "-p",
            "no:cacheprovider",
            target,
        ],
        cwd=CORE,
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=300,
    )
    failed = [ln for ln in r.stdout.splitlines() if ln.startswith(("FAILED", "ERROR"))]
    name = failed[0].split(" ")[1].split("::")[-1] if failed else ""
    return r.returncode != 0, name


def main() -> int:
    rows = ["| Invariant | Mutation | File | Result |", "|---|---|---|---|"]
    survived = 0
    for m in MUTATIONS:
        path = CORE / m.file
        original = path.read_text(encoding="utf-8")
        if original.count(m.old) < 1:
            print(f"{m.invariant}: line not found in {m.file}")
            return 2
        try:
            path.write_text(original.replace(m.old, m.new, 1), encoding="utf-8")
            died, test = run(m.target)
        finally:
            path.write_text(original, encoding="utf-8")
        survived += not died
        verdict = f"failed `{test}`" if died else "**survived**"
        print(f"{m.invariant}: {verdict}", flush=True)
        rows.append(f"| {m.invariant} | {m.what} | `{m.file}` | {verdict} |")
    doc = INVARIANTS_DOC.read_text(encoding="utf-8")
    head = doc.split("<!-- mutations -->")[0]
    INVARIANTS_DOC.write_text(
        head + "<!-- mutations -->\n\n" + "\n".join(rows) + "\n", encoding="utf-8"
    )
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
