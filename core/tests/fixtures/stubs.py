"""Third-party stub: Ollama, OpenRouter, Tavily (R1). The only seam of the groups is the transport.

A request to an unknown route gets an explicit error and is recorded in ``unexpected``: the
``backends`` fixture fails such a test on exit (R12). The SDK would take an ``AssertionError`` from
the transport for a network failure.
"""

import asyncio
import json
from typing import Any

import httpx

from tests.fixtures.corpus import NO_HITS_REPLY, REPLY, fake_vector

PULL_LINES = [
    {"status": "pulling manifest"},
    {"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 500},
    {"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 1000},
    {"status": "verifying sha256 digest"},
    {"status": "success"},
]
WEB_RESULTS = [
    {"title": "DOL", "url": "https://www.dol.gov/vacation", "content": "28 days", "score": 0.8}
]
RUNNER_STOPPED = "model runner has unexpectedly stopped, this may be due to resource limitations"


class Backends:
    """Call logs and reply scenarios; a test may change the fields before the request."""

    def __init__(self) -> None:
        self.chat: list[dict[str, Any]] = []  # Ollama /api/chat request bodies
        self.openrouter: list[dict[str, Any]] = []  # {"auth", "body"}
        self.tavily: list[dict[str, Any]] = []  # {"auth", "body"}
        self.unloads: list[dict[str, Any]] = []
        self.unexpected: list[str] = []
        # Ollama scenario: with tools, call the first one; after its result, answer
        self.reply: list[str] = list(REPLY)
        self.tool_query: str | None = None  # None means the text of the last user message
        self.tool_call: tuple[str, dict[str, Any]] | None = None  # call this tool
        self.usage: tuple[int, int] = (321, 12)
        self.chat_failures = 0  # this many first calls answer 500: the runner was killed
        self.chat_gate: asyncio.Event | None = (
            None  # if set, /api/chat waits for it: a "long" answer
        )
        self.chat_stream_error: str | None = (
            None  # if set, the stream breaks with this Ollama error
        )
        self.tags: list[str] = ["embeddinggemma-2:570m"]
        self.capabilities: list[str] = ["completion", "tools"]  # /api/show of the loaded model
        self.pulls: list[str] = []  # names from /api/pull
        self.openrouter_reply = "External answer [1]."
        self.openrouter_usage: tuple[int, int] = (1000, 100)
        self.openrouter_keys: set[str] = {"sk-or-valid-key"}
        self.openrouter_denied = False
        self.openrouter_models: list[dict[str, Any]] = []
        self.tavily_results = WEB_RESULTS
        self.tavily_status = 200  # 401 a bad key, 403 the server's address refused, 429 the limit
        # fake GitHub and sandbox-runner
        self.github: list[tuple[str, str, Any]] = []
        self.github_commits: list[str] = ["abc123"]
        self.github_failures: set[str] = set()  # path substrings that answer 500
        # connected agent endpoint (yorath_agents network 172.31.0.0/24): OpenAI Chat Completions
        self.connected: list[dict[str, Any]] = []  # {"url", "auth", "body"}
        self.connected_reply: list[str] = ["Par", "is"]
        self.connected_down = False  # container not running: connection refused
        # cluster worker node gateway in the tunnel (10.88.0.0/24, installer/Caddyfile.node)
        self.node_gateway: list[str] = []  # "<node> <path>"
        self.node_servers: set[str] = {"cpu", "gpu0", "gpu1"}  # model servers the nodes run
        self.node_down: set[str] = set()  # tunnel addresses that refuse the connection
        self.refused: list[str] = []  # "<address> <path>" of the refused connections
        self.sandbox_runs: list[dict[str, Any]] = []
        self.sandbox_lines: list[dict[str, Any]] = [
            {"started": True, "ip": "172.30.0.9"},
            {"line": '{"type":"thread.started","thread_id":"t1"}'},
            {"line": '{"type":"item.completed","item":{"type":"agent_message","text":"Done"}}'},
            {"line": '{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":2}}'},
            {"exit": 0, "timed_out": False, "stderr": ""},
        ]

    def client(self) -> httpx.AsyncClient:
        async def handle(request: httpx.Request) -> httpx.Response:
            # also behind a node gateway: /ollama/<server>/api/chat
            if self.chat_gate is not None and request.url.path.endswith("/api/chat"):
                await self.chat_gate.wait()
            if request.url.host in self.node_down:
                self.refused.append(f"{request.url.host} {request.url.path}")
                raise httpx.ConnectError("connection refused", request=request)
            if request.url.host.startswith("172.31.0.") and self.connected_down:
                raise httpx.ConnectError("connection refused", request=request)
            return self(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handle))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "gh.test":
            return self._github(request)
        if request.url.host.startswith("10.88.0."):
            return self._node_gateway(request)
        if request.url.host.startswith("172.31.0."):
            return self._connected(request)
        if path == "/runs":
            run = json.loads(request.content)
            self.sandbox_runs.append(run)
            lines = "".join(json.dumps(x) + "\n" for x in self.sandbox_lines)
            lines = lines.replace("{execution_id}", str(run.get("execution_id", "")))
            return httpx.Response(200, text=lines)
        if path == "/api/embed":
            texts = json.loads(request.content)["input"]
            return httpx.Response(200, json={"embeddings": [fake_vector(t) for t in texts]})
        if path == "/api/chat":
            return self._ollama_chat(json.loads(request.content))
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in self.tags]})
        if path == "/api/show":
            return httpx.Response(200, json={"capabilities": self.capabilities})
        if path == "/api/pull":
            self.pulls.append(json.loads(request.content)["model"])
            body = "".join(json.dumps(line) + "\n" for line in PULL_LINES)
            return httpx.Response(200, content=body.encode())
        if path == "/api/generate":
            self.unloads.append(json.loads(request.content))
            return httpx.Response(200, json={"done": True})
        if path.endswith("/chat/completions"):
            return self._openrouter_chat(request)
        if path.endswith("/models"):
            return self._openrouter_models(request)
        if path == "/search":
            self.tavily.append(
                {"auth": request.headers.get("authorization"), "body": json.loads(request.content)}
            )
            if self.tavily_status != 200:
                return httpx.Response(self.tavily_status, text="<html>Forbidden</html>")
            return httpx.Response(200, json={"results": self.tavily_results})
        self.unexpected.append(f"{request.method} {request.url}")
        return httpx.Response(599, text=f"the stub does not know route {path}")

    def _node_gateway(self, request: httpx.Request) -> httpx.Response:
        """Node gateway: /ollama/<server>/* is a node Ollama, /agents/<address>:<port>/* the node agent endpoint."""
        path = request.url.path
        self.node_gateway.append(f"{request.url.host} {path}")
        if path.startswith("/ollama/"):
            # /ollama/<model server>/…: one Ollama per card (installer/Caddyfile.node)
            server, _, rest = path.removeprefix("/ollama/").partition("/")
            if server not in self.node_servers:
                return httpx.Response(404, text=f"node has no model server {server}")
            inner = httpx.Request(request.method, f"http://ollama-{server}:11434/{rest}",
                                  content=request.content, headers=request.headers)  # fmt: skip
            return self(inner)
        if path.startswith("/agents/"):
            target, _, rest = path.removeprefix("/agents/").partition("/")
            inner = httpx.Request(request.method, f"http://{target}/{rest}",
                                  content=request.content, headers=request.headers)  # fmt: skip
            return self._connected(inner)
        self.unexpected.append(f"{request.method} {request.url}")
        return httpx.Response(599, text=f"the node gateway does not know {path}")

    def _connected(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        auth = request.headers.get("authorization")
        self.connected.append({"url": str(request.url), "auth": auth, "body": body})
        base = {"object": "chat.completion.chunk", "model": body.get("model")}
        events = [{**base, "choices": [{"delta": {"content": t}}]} for t in self.connected_reply]
        events.append({**base, "choices": [{"delta": {}, "finish_reason": "stop"}]})
        sse = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode())

    def _github(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.github.append((request.method, path, body))
        if any(part in path for part in self.github_failures):
            return httpx.Response(500, json={})
        if path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs_secret_token"})
        if "/compare/" in path:
            return httpx.Response(200, json={"commits": [{"sha": c} for c in self.github_commits]})
        if path.endswith("/pulls"):
            return httpx.Response(201, json={"number": 7, "html_url": "https://gh/pull/7"})
        return httpx.Response(204 if request.method == "DELETE" else 201, json={})

    def _ollama_chat(self, body: dict[str, Any]) -> httpx.Response:
        self.chat.append(body)
        if self.chat_failures > 0:
            self.chat_failures -= 1
            return httpx.Response(500, json={"error": RUNNER_STOPPED})
        last = body["messages"][-1]
        done = {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": self.usage[0],
            "eval_count": self.usage[1],
            "model": body["model"],
        }
        if body.get("tools") and last["role"] != "tool":
            query = self.tool_query or next(
                m["content"] for m in reversed(body["messages"]) if m["role"] == "user"
            )
            name, args = self.tool_call or (body["tools"][0]["function"]["name"], {"query": query})
            call = {"id": "call_1", "function": {"index": 0, "name": name, "arguments": args}}
            events: list[dict[str, Any]] = [
                {"message": {"content": "", "tool_calls": [call]}, "done": False}
            ]
        elif last["role"] == "tool" and "match the query" in last["content"]:
            events = [{"message": {"content": NO_HITS_REPLY}, "done": False}]
        else:
            events = [{"message": {"content": t}, "done": False} for t in self.reply]
        tail = {"error": self.chat_stream_error} if self.chat_stream_error else done
        lines = [json.dumps(e) for e in [*events, tail]]
        return httpx.Response(200, content=("\n".join(lines) + "\n").encode())

    def _openrouter_chat(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.openrouter.append({"auth": request.headers.get("authorization"), "body": body})
        if body.get("tools") and body["messages"][-1]["role"] != "tool":
            query = next(m["content"] for m in reversed(body["messages"]) if m["role"] == "user")
            name = body["tools"][0]["function"]["name"]
            args = json.dumps({"query": query})
            call = {"index": 0, "id": "c1", "function": {"name": name, "arguments": args}}
            delta: dict[str, Any] = {"tool_calls": [call]}
            finish = "tool_calls"
        else:
            delta, finish = {"content": self.openrouter_reply}, "stop"
        prompt, completion = self.openrouter_usage
        events = [
            {"model": body["model"], "choices": [{"delta": delta, "finish_reason": finish}]},
            {"choices": [], "usage": {"prompt_tokens": prompt, "completion_tokens": completion}},
        ]
        sse = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode())

    def _openrouter_models(self, request: httpx.Request) -> httpx.Response:
        key = request.headers.get("authorization", "").removeprefix("Bearer ")
        if self.openrouter_denied:  # this is how OpenRouter answers a server from a closed region
            return httpx.Response(
                403, json={"success": False, "error": "Access denied by security policy."}
            )
        if key not in self.openrouter_keys:
            return httpx.Response(401, json={"error": "bad key"})
        return httpx.Response(200, json={"data": self.openrouter_models})
