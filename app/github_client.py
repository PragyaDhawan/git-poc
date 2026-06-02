import logging
import time
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
    """

    def __init__(
        self,
        app_id: str,
        installation_id: str,
        private_key_path: str,
        timeout: float = 10.0,
    ):
        if not app_id:
            raise ValueError("GITHUB_APP_ID is required.")
        if not installation_id:
            raise ValueError("GITHUB_INSTALLATION_ID is required.")
        if not private_key_path:
            raise ValueError("GITHUB_PRIVATE_KEY_PATH is required.")

        self.app_id = str(app_id)
        self.installation_id = str(installation_id)
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

        self._installation_token: str | None = None
        self._installation_token_expires_at: float = 0.0

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

    async def _exchange_installation_token(self) -> str:
        jwt_token = self._create_jwt()

        resp = await self._http.post(
            f"/app/installations/{self.installation_id}/access_tokens",
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
            # Keep a safety margin so we refresh before expiry.
            self._installation_token_expires_at = (
                time.mktime(time.strptime(expires_at[:19], "%Y-%m-%dT%H:%M:%S")) - 60
            )
        else:
            self._installation_token_expires_at = time.time() + 50 * 60

        self._installation_token = token
        logger.info("Fetched installation token for installation %s", self.installation_id)
        return token

    async def _get_installation_token(self) -> str:
        if (
            self._installation_token
            and time.time() < self._installation_token_expires_at
        ):
            return self._installation_token

        return await self._exchange_installation_token()

    async def _auth_headers(self) -> dict[str, str]:
        token = await self._get_installation_token()
        return {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------ #
    #  Internal GitHub API helper                                        #
    # ------------------------------------------------------------------ #

    async def _post(self, path: str, body: dict) -> dict:
        try:
            resp = await self._http.post(
                path,
                json=body,
                headers=await self._auth_headers(),
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

    async def post_pr_comment(self, repo: str, pr_number: int, body: str) -> dict:
        result = await self._post(
            f"/repos/{repo}/issues/{pr_number}/comments",
            {"body": body},
        )
        logger.info("Posted comment on PR #%s in %s", pr_number, repo)
        return result

    # ------------------------------------------------------------------ #
    #  Labels                                                            #
    # ------------------------------------------------------------------ #

    async def add_labels(self, repo: str, pr_number: int, labels: list[str]) -> dict:
        return await self._post(
            f"/repos/{repo}/issues/{pr_number}/labels",
            {"labels": labels},
        )

    # ------------------------------------------------------------------ #
    #  Review requests                                                   #
    # ------------------------------------------------------------------ #

    async def request_reviewers(
        self, repo: str, pr_number: int, reviewers: list[str]
    ) -> dict:
        return await self._post(
            f"/repos/{repo}/pulls/{pr_number}/requested_reviewers",
            {"reviewers": reviewers},
        )

    # ------------------------------------------------------------------ #
    #  Commit statuses                                                   #
    # ------------------------------------------------------------------ #

    async def set_commit_status(
        self,
        repo: str,
        sha: str,
        state: CommitState,
        description: str = "",
        context: str = "webhook-bot",
        target_url: str = "",
    ) -> dict:
        body: dict = {"state": state, "description": description, "context": context}
        if target_url:
            body["target_url"] = target_url
        return await self._post(f"/repos/{repo}/statuses/{sha}", body)