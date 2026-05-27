import logging
from typing import Literal

import httpx

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
    Async GitHub REST API client.

    Uses a single httpx.AsyncClient for connection reuse across calls.
    Call `await client.close()` (or use as an async context manager) when done.
    """

    def __init__(self, token: str, timeout: float = 10.0):
        if not token:
            raise ValueError(
                "GITHUB_TOKEN is required. "
                "Create one at Settings → Developer settings → Personal access tokens."
            )
        self._http = httpx.AsyncClient(
            base_url=GITHUB_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    async def _post(self, path: str, body: dict) -> dict:
        """POST to a GitHub API path; raises GitHubAPIError on non-2xx."""
        try:
            resp = await self._http.post(path, json=body)
            resp.raise_for_status()
            return resp.json()
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
    #  Comments                                                            #
    # ------------------------------------------------------------------ #

    async def post_pr_comment(self, repo: str, pr_number: int, body: str) -> dict:
        """Post a comment on a pull request."""
        result = await self._post(
            f"/repos/{repo}/issues/{pr_number}/comments",
            {"body": body},
        )
        logger.info("Posted comment on PR #%s in %s", pr_number, repo)
        return result

    # ------------------------------------------------------------------ #
    #  Labels                                                              #
    # ------------------------------------------------------------------ #

    async def add_labels(self, repo: str, pr_number: int, labels: list[str]) -> dict:
        """Add labels to a PR / issue."""
        return await self._post(
            f"/repos/{repo}/issues/{pr_number}/labels",
            {"labels": labels},
        )

    # ------------------------------------------------------------------ #
    #  Review requests                                                     #
    # ------------------------------------------------------------------ #

    async def request_reviewers(
        self, repo: str, pr_number: int, reviewers: list[str]
    ) -> dict:
        """Request reviewers for a PR."""
        return await self._post(
            f"/repos/{repo}/pulls/{pr_number}/requested_reviewers",
            {"reviewers": reviewers},
        )

    # ------------------------------------------------------------------ #
    #  Commit statuses                                                     #
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
        """Set a status check on a commit SHA."""
        body: dict = {"state": state, "description": description, "context": context}
        if target_url:
            body["target_url"] = target_url
        return await self._post(f"/repos/{repo}/statuses/{sha}", body)