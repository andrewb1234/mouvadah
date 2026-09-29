"""Fixed-origin, bounded GitHub App API access. No tokens are persisted."""

from __future__ import annotations

import re
import time
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException

from api.config import get_settings

API = "https://api.github.com"
CALLBACK = "/api/v1/github/callback"


class GitHubFailure(Exception):
    def __init__(self, code: str, retry_after: int = 60):
        self.code = code
        self.retry_after = min(max(retry_after, 60), 86400)
        super().__init__(code)


def configured() -> bool:
    s = get_settings()
    return bool(s.github_app_enabled and s.github_app_id and s.github_app_id > 0 and s.github_app_slug
                and re.fullmatch(r"[a-zA-Z0-9-]+", s.github_app_slug)
                and s.github_app_private_key and s.github_app_client_id
                and s.github_app_client_secret and 1 <= len(s.github_webhook_secrets) <= 3
                and all(len(key.get_secret_value()) >= 32 for key in s.github_webhook_secrets))


def require_configured():
    if not configured():
        raise HTTPException(503, "GitHub App is not configured on this server.")
    return get_settings()


def _json(method: str, path: str, token: str, **kwargs):
    # Paths are constructed from numeric IDs or validated repo names, never URLs
    # supplied by a webhook. Redirects could carry credentials to another host.
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = client.request(method, API + path, headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }, **kwargs)
    except httpx.HTTPError:
        raise GitHubFailure("provider_unreachable") from None
    if response.status_code == 404:
        raise GitHubFailure("access_revoked")
    if response.status_code in {403, 429}:
        retry = response.headers.get("retry-after", "60")
        reset = response.headers.get("x-ratelimit-reset", "0")
        seconds = max(int(retry) if retry.isdigit() else 60,
                      int(reset) - int(time.time()) if reset.isdigit() else 60)
        raise GitHubFailure("provider_rate_or_permission_limit", seconds)
    if not 200 <= response.status_code < 300:
        raise GitHubFailure("provider_request_failed")
    try:
        return response.json()
    except ValueError:
        raise GitHubFailure("invalid_provider_response") from None


def app_token() -> str:
    s = require_configured()
    try:
        return jwt.encode({"iat": int(time.time()) - 60, "exp": int(time.time()) + 540,
                           "iss": str(s.github_app_id)},
                          s.github_app_private_key.get_secret_value(), algorithm="RS256")
    except (ValueError, jwt.PyJWTError):
        raise GitHubFailure("invalid_app_configuration") from None


def installation_info(installation_id: int) -> dict:
    return _json("GET", f"/app/installations/{installation_id}", app_token())


def installation_token(installation_id: int, repository_id: int) -> str:
    data = _json("POST", f"/app/installations/{installation_id}/access_tokens", app_token(),
                 json={"repository_ids": [repository_id],
                       "permissions": {"metadata": "read", "pull_requests": "read", "issues": "read"}})
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise GitHubFailure("invalid_provider_response")
    return token


def authorization_url(state: str) -> str:
    s = require_configured()
    return "https://github.com/login/oauth/authorize?" + urlencode({
        "client_id": s.github_app_client_id, "state": state,
        "redirect_uri": s.public_origin() + CALLBACK,
    })


def user_token(code: str) -> str:
    s = require_configured()
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = client.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"}, json={
                "client_id": s.github_app_client_id,
                "client_secret": s.github_app_client_secret.get_secret_value(),
                "code": code, "redirect_uri": s.public_origin() + CALLBACK,
            })
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise GitHubFailure("oauth_exchange_failed") from None
    if response.status_code != 200 or not data.get("access_token"):
        raise GitHubFailure("oauth_exchange_failed")
    return data["access_token"]


def paginated(path: str, token: str, key: str) -> list[dict]:
    result = []
    # Fail closed rather than silently authorize a truncated inventory.
    for page in range(1, 11):
        data = _json("GET", path, token, params={"per_page": 100, "page": page})
        rows = data.get(key, [])
        if not isinstance(rows, list):
            raise GitHubFailure("invalid_provider_response")
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise GitHubFailure("inventory_limit_exceeded")


def authorized_repositories(token: str, installation_id: int) -> tuple[dict, list[dict]]:
    installations = paginated("/user/installations", token, "installations")
    installation = next((i for i in installations if i.get("id") == installation_id), None)
    if not installation or installation.get("suspended_at"):
        raise GitHubFailure("installation_not_authorized")
    repos = paginated(f"/user/installations/{installation_id}/repositories", token, "repositories")
    allowed = []
    for repo in repos:
        # The user-scoped endpoint reports only repositories this user can see;
        # require administrator permission before delegating them to a workspace.
        if repo.get("permissions", {}).get("admin") is True:
            allowed.append({"id": repo["id"], "full_name": repo["full_name"]})
    if not allowed:
        raise GitHubFailure("repository_admin_required")
    return installation, allowed


def repository_info(token: str, repository_id: int) -> dict:
    data = _json("GET", f"/repositories/{repository_id}", token)
    if data.get("id") != repository_id or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", data.get("full_name", "")):
        raise GitHubFailure("invalid_provider_response")
    return data


def object_info(token: str, full_name: str, kind: str, number: int) -> dict:
    if kind not in {"pull", "issue"} or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
        raise GitHubFailure("invalid_provider_response")
    data = _json("GET", f"/repos/{full_name}/{'pulls' if kind == 'pull' else 'issues'}/{number}", token)
    if data.get("number") != number or (kind == "issue" and "pull_request" in data):
        raise GitHubFailure("object_kind_mismatch")
    return {
        "title": str(data.get("title", ""))[:1000],
        "state": "merged" if data.get("merged") else data.get("state", "unknown"),
        "url": f"https://github.com/{full_name}/{'pull' if kind == 'pull' else 'issues'}/{number}",
        "head_sha": data.get("head", {}).get("sha") if kind == "pull" else None,
        "provider_updated_at": data.get("updated_at"),
    }
