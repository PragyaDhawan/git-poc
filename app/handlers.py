import logging

from fastapi.responses import JSONResponse

from app.github_client import GitHubClient, GitHubAPIError
from app.models import PRPayload, PushPayload, PingPayload

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  ping                                                                        #
# --------------------------------------------------------------------------- #

async def handle_ping(payload: PingPayload) -> JSONResponse:
    """GitHub sends a ping when a webhook is first configured."""
    logger.info("Ping received — hook_id=%s zen=%s", payload.hook_id, payload.zen)
    return JSONResponse(content={"message": "pong", "zen": payload.zen})


# --------------------------------------------------------------------------- #
#  pull_request                                                                #
# --------------------------------------------------------------------------- #

async def handle_pull_request(payload: PRPayload, client: GitHubClient) -> JSONResponse:
    """
    Handle pull_request events.

    Actions handled:
      opened           → welcome comment + 'needs-review' label + pending status
      synchronize      → notify that new commits were pushed
      closed           → thank (merged) or note closure
      review_requested → ping the reviewer
    """
    action = payload.action
    pr = payload.pull_request
    repo = payload.repository.full_name
    installation_id = payload.installation.id

    logger.info("PR #%s '%s' by @%s — action: %s", pr.number, pr.title, pr.user.login, action)

    try:
        match action:
            case "opened":
                comment = (
                    f"👋 Thanks for opening this PR, @{pr.user.login}!\n\n"
                    f"**Title:** {pr.title}\n\n"
                    "A reviewer will take a look shortly. "
                    "Please make sure all checks pass before requesting a review. ✅"
                )
                await client.post_pr_comment(repo, pr.number, comment, installation_id)
                await client.add_labels(repo, pr.number, ["needs-review"], installation_id)
                await client.set_commit_status(
                    repo, pr.head.sha,
                    state="pending",
                    installation_id=installation_id,
                    description="Awaiting review",
                    context="webhook-bot/review",
                )
                return JSONResponse(content={"message": "Welcomed PR author and added label"})

            case "synchronize":
                comment = (
                    f"🔄 @{pr.user.login} pushed new commits to this PR. "
                    "Re-running checks…"
                )
                await client.post_pr_comment(repo, pr.number, comment, installation_id)
                await client.set_commit_status(
                    repo, pr.head.sha,
                    state="pending",
                    installation_id=installation_id,
                    description="Checking new commits",
                    context="webhook-bot/review",
                )
                return JSONResponse(content={"message": "Noted new commits"})

            case "closed":
                comment = (
                    f"🎉 PR merged! Great work, @{pr.user.login}. "
                    "Your changes are now part of the codebase."
                    if pr.merged else
                    f"🚪 PR closed without merging. @{pr.user.login}, feel free to reopen if needed."
                )
                await client.post_pr_comment(repo, pr.number, comment, installation_id)
                return JSONResponse(content={"message": "Handled PR closure"})

            case "review_requested":
                reviewer = payload.requested_reviewer.login if payload.requested_reviewer else "someone"
                await client.post_pr_comment(repo, pr.number, f"👀 Review requested from @{reviewer}.")
                return JSONResponse(content={"message": "Noted review request"})

            case _:
                return JSONResponse(content={"message": f"PR action '{action}' received but not handled"})

    except GitHubAPIError as exc:
        logger.error("GitHub API error for PR #%s: %s", pr.number, exc)
        return JSONResponse(
            status_code=502,
            content={
                "message": "Failed to call GitHub API",
                "status_code": exc.status_code,
                "detail": exc.detail,
            },
        )


# --------------------------------------------------------------------------- #
#  push                                                                        #
# --------------------------------------------------------------------------- #

async def handle_push(payload: PushPayload, client: GitHubClient) -> JSONResponse:
    """Log push events (extend to trigger CI, notify Slack, etc.)."""
    logger.info(
        "Push to %s in %s by %s — %d commit(s)",
        payload.ref, payload.repository.full_name, payload.pusher.name, len(payload.commits),
    )
    return JSONResponse(
        content={
            "message": "Push event received",
            "pusher": payload.pusher.name,
            "ref": payload.ref,
            "commits": len(payload.commits),
        }
    )