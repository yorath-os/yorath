"""A bound on what a worker node may POST to ``/node/*`` (change pre-public-hardening, S6).

The body is read up to the limit before the route sees it: a larger one is answered 413
``payload_too_large`` whether or not the client sent ``Content-Length``.
"""

import json
from typing import Any

from yorath.runtime.connected.limits import NODE_BODY_BYTES


class NodeBodyLimit:
    def __init__(self, app: Any, prefix: str = "/node/", limit: int = NODE_BODY_BYTES) -> None:
        self.app, self.prefix, self.limit = app, prefix, limit

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].startswith(self.prefix)
        ):
            await self.app(scope, receive, send)
            return
        body, more = b"", True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > self.limit:
                await _too_large(send, self.limit)
                return
        sent = False

        async def replay() -> dict:
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)


async def _too_large(send: Any, limit: int) -> None:
    message = f"a node request is at most {limit} bytes"
    payload = json.dumps({"detail": {"code": "payload_too_large", "message": message}}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode())]})  # fmt: skip
    await send({"type": "http.response.body", "body": payload})
