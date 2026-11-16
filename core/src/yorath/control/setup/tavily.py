"""Tavily: web search for ``web_search``. The key lives only in the SecretStore, by consent."""

from typing import Literal

import httpx

SECRET_NAME = "tavily_api_key"
LEAVES_SERVER = (
    "When searching the web, the query text written by the model leaves the server. "
    "With a Confidential or Restricted context, search is blocked by policy."
)

Answer = Literal["ok", "invalid", "denied", "limit", "unreachable", "error"]

# what each answer means, for the model (tool error), the wizard and the docs (field-fixes-0-7)
REASONS: dict[str, str] = {
    "invalid": "the Tavily key is invalid or revoked: set a new key in the setup wizard",
    "denied": (
        "Tavily refused this server's address (403) although the key is valid: the service may "
        "not serve the server's country. Search works once the server reaches Tavily from another "
        "region"
    ),
    "limit": "the Tavily limit is used up (429): wait or raise the plan",
    "unreachable": "Tavily is not reachable from this server",
    "error": "Tavily answered with an error",
}


def classify(status: int | None) -> Answer:
    """An HTTP status of Tavily (None: no answer) → what it means."""
    if status is None:
        return "unreachable"
    if 200 <= status < 300:
        return "ok"
    known: dict[int, Answer] = {401: "invalid", 403: "denied", 429: "limit"}
    return known.get(status, "error")


async def probe(http: httpx.AsyncClient, base_url: str, api_key: str) -> Answer:
    """One search of one result with a fixed query: it spends one credit, sends no user data."""
    try:
        r = await http.post(
            f"{base_url.rstrip('/')}/search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": "test", "max_results": 1},
            timeout=15,
        )
    except httpx.HTTPError:
        return "unreachable"
    return classify(r.status_code)
