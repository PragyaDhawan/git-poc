import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
import jwt

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
CommitState = Literal["error", "failure", "pending", "success"]


class GitHubAPIError(Exception):
    """Raised when a GitHub API call fails."""

    def __init__(self, method: str, url: str, status_code: int, detail: str):
        self.method = method
        self.url = url
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"GitHub API {method} {url} → {status_code}: {detail}")


class GitHubClient:
    """
    Async GitHub REST API client for a GitHub App.

    Flow:
      1. Create JWT using App ID + private key
      2. Exchange JWT for installation access token
      3. Use installation token for API calls

    Supports multiple installations by caching tokens per installation_id.
    """

    def __init__(
        self,
        app_id: str,
        private_key_path: str,
        timeout: float = 10.0,
    ):
        if not app_id:
            raise ValueError("GITHUB_APP_ID is required.")
        if not private_key_path:
            raise ValueError("GITHUB_PRIVATE_KEY_PATH is required.")

        self.app_id = str(app_id)
        self.private_key_path = Path(private_key_path)

        if not self.private_key_path.exists():
            raise FileNotFoundError(
                f"Private key file not found: {self.private_key_path}"
            )

        self._http = httpx.AsyncClient(
            base_url=GITHUB_API,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
        )

        # Cache installation tokens per installation_id:
        # { installation_id: (token, expires_at_unix_timestamp) }
        self._installation_tokens: dict[int, tuple[str, float]] = {}

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    # ------------------------------------------------------------------ #
    #  Auth                                                              #
    # ------------------------------------------------------------------ #

    def _load_private_key(self) -> str:
        return self.private_key_path.read_text(encoding="utf-8")

    def _create_jwt(self) -> str:
        """
        GitHub App JWT must:
          - be signed with RS256
          - have iat in the past or now
          - have exp no more than 10 minutes in the future
        """
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 8 * 60,
            "iss": self.app_id,
        }
        private_key = self._load_private_key()
        token = jwt.encode(payload, private_key, algorithm="RS256")
        return token if isinstance(token, str) else token.decode("utf-8")

    @staticmethod
    def _parse_expiry(expires_at: str) -> float:
        """
        GitHub returns ISO-8601, usually like:
          2026-06-02T12:34:56Z
        Convert to a UNIX timestamp with a small safety margin.
        """
        normalized = expires_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.astimezone(timezone.utc).timestamp() - 60

    async def _exchange_installation_token(self, installation_id: int) -> str:
        jwt_token = self._create_jwt()

        resp = await self._http.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
            },
            json={},
        )

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GitHubAPIError(
                method="POST",
                url=str(exc.request.url),
                status_code=exc.response.status_code,
                detail=exc.response.text,
            ) from exc

        data = resp.json()
        token = data["token"]
        expires_at = data.get("expires_at")

        if expires_at:
            expiry_ts = self._parse_expiry(expires_at)
        else:
            # Fallback: GitHub installation tokens usually last about 1 hour
            expiry_ts = time.time() + 50 * 60

        self._installation_tokens[installation_id] = (token, expiry_ts)
        logger.info("Fetched installation token for installation %s", installation_id)
        return token

    async def _get_installation_token(self, installation_id: int) -> str:
        cached = self._installation_tokens.get(installation_id)
        if cached:
            token, expires_at = cached
            if time.time() < expires_at:
                return token

        return await self._exchange_installation_token(installation_id)

    async def _auth_headers(self, installation_id: int) -> dict[str, str]:
        token = await self._get_installation_token(installation_id)
        return {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------ #
    #  Internal GitHub API helper                                        #
    # ------------------------------------------------------------------ #

    async def _post(self, path: str, body: dict, installation_id: int) -> dict:
        try:
            resp = await self._http.post(
                path,
                json=body,
                headers=await self._auth_headers(installation_id),
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        except httpx.HTTPStatusError as exc:
            raise GitHubAPIError(
                method="POST",
                url=str(exc.request.url),
                status_code=exc.response.status_code,
                detail=exc.response.text,
            ) from exc
        except httpx.RequestError as exc:
            raise GitHubAPIError(
                method="POST",
                url=str(exc.request.url),
                status_code=0,
                detail=str(exc),
            ) from exc

    # ------------------------------------------------------------------ #
    #  Comments                                                          #
    # ------------------------------------------------------------------ #

    async def post_pr_comment(
        self,
        repo: str,
        pr_number: int,
        body: str,
        installation_id: int,
    ) -> dict:
        result = await self._post(
            f"/repos/{repo}/issues/{pr_number}/comments",
            {"body": body},
            installation_id,
        )
        logger.info("Posted comment on PR #%s in %s", pr_number, repo)
        return result

    # ------------------------------------------------------------------ #
    #  Labels                                                            #
    # ------------------------------------------------------------------ #

    async def add_labels(
        self,
        repo: str,
        pr_number: int,
        labels: list[str],
        installation_id: int,
    ) -> dict:
        return await self._post(
            f"/repos/{repo}/issues/{pr_number}/labels",
            {"labels": labels},
            installation_id,
        )

    # ------------------------------------------------------------------ #
    #  Review requests                                                   #
    # ------------------------------------------------------------------ #

    async def request_reviewers(
        self,
        repo: str,
        pr_number: int,
        reviewers: list[str],
        installation_id: int,
    ) -> dict:
        return await self._post(
            f"/repos/{repo}/pulls/{pr_number}/requested_reviewers",
            {"reviewers": reviewers},
            installation_id,
        )

    # ------------------------------------------------------------------ #
    #  Commit statuses                                                   #
    # ------------------------------------------------------------------ #

    async def set_commit_status(
        self,
        repo: str,
        sha: str,
        state: CommitState,
        installation_id: int,
        description: str = "",
        context: str = "webhook-bot",
        target_url: str = "",
    ) -> dict:
        body: dict = {"state": state, "description": description, "context": context}
        if target_url:
            body["target_url"] = target_url

        return await self._post(
            f"/repos/{repo}/statuses/{sha}",
            body,
            installation_id,
        )