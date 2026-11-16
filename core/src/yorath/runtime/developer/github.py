"""GitHub App: an installation token for one repository and the REST API requests we need."""

import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import jwt
from pydantic import BaseModel, Field

from yorath.enforcement.secrets import SecretStore

SECRET_NAME = "github.app"
API_VERSION = "2022-11-28"
PERMISSIONS = {"contents": "write", "pull_requests": "write", "issues": "write"}


class GitHubConfig(BaseModel):
    app_id: str = Field(min_length=1, max_length=50)
    installation_id: str = Field(min_length=1, max_length=50)
    private_key: str = Field(min_length=1)
    webhook_secret: str = Field(min_length=1)
    repositories: list[str] = []


async def load_config(store: SecretStore) -> GitHubConfig | None:
    raw = await store.get(SECRET_NAME)
    return GitHubConfig.model_validate_json(raw) if raw else None


def valid_signature(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, http: httpx.AsyncClient, base_url: str, config: GitHubConfig) -> None:
        self._http, self._base, self._config = http, base_url.rstrip("/"), config

    def _app_jwt(self) -> str:
        now = int(time.time())
        claims = {"iat": now - 60, "exp": now + 540, "iss": self._config.app_id}
        return jwt.encode(claims, self._config.private_key, algorithm="RS256")

    async def _call(self, method: str, path: str, token: str, body: Any = None) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        content = None if body is None else json.dumps(body)
        r = await self._http.request(
            method, f"{self._base}{path}", headers=headers, content=content, timeout=30
        )
        if r.status_code >= 400:
            raise GitHubError(f"GitHub {method} {path}: HTTP {r.status_code}")
        return r

    async def repo_token(self, repository: str) -> str:
        """An installation token for this repository only."""
        name = repository.split("/", 1)[1]
        r = await self._call(
            "POST",
            f"/app/installations/{self._config.installation_id}/access_tokens",
            self._app_jwt(),
            {"repositories": [name], "permissions": PERMISSIONS},
        )
        return str(r.json()["token"])

    async def revoke(self, token: str) -> None:
        await self._call("DELETE", "/installation/token", token)

    async def ahead_commits(self, token: str, repo: str, base: str, branch: str) -> list[str]:
        """Branch commits ahead of the base branch. No branch means empty: there is no proof."""
        try:
            r = await self._call("GET", f"/repos/{repo}/compare/{base}...{branch}", token)
        except GitHubError:
            return []
        return [str(c["sha"]) for c in r.json().get("commits", [])]

    async def open_pull_request(
        self, token: str, repo: str, base: str, branch: str, title: str, body: str
    ) -> dict[str, Any]:
        data = {"title": title, "head": branch, "base": base, "body": body}
        return (await self._call("POST", f"/repos/{repo}/pulls", token, data)).json()

    async def comment(self, token: str, repo: str, issue: int, text: str) -> None:
        await self._call("POST", f"/repos/{repo}/issues/{issue}/comments", token, {"body": text})
