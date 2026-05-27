import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.github_client import GitHubClient, GitHubAPIError
from app.main import app, get_github_client

# ------------------------------------------------------------------ #
#  Shared mock — injected via FastAPI dependency override             #
# ------------------------------------------------------------------ #

mock_gh = AsyncMock(spec=GitHubClient)
app.dependency_overrides[get_github_client] = lambda: mock_gh


@pytest.fixture(autouse=True)
def reset_mock():
    """Clear call history and side effects between every test."""
    mock_gh.reset_mock()
    mock_gh.post_pr_comment.side_effect = None
    mock_gh.add_labels.side_effect = None
    mock_gh.set_commit_status.side_effect = None


@pytest.fixture(scope="module")
def test_client():
    with TestClient(app) as c:
        yield c


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def sign(payload: bytes, secret: str = "test-secret") -> str:
    return "sha256=" + hmac.digest(secret.encode(), payload, hashlib.sha256).hex()


def post_event(client, event: str, payload: dict, secret: str = "test-secret"):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": sign(body, secret),
        },
    )


# ------------------------------------------------------------------ #
#  Fixtures                                                            #
# ------------------------------------------------------------------ #

PR_PAYLOAD = {
    "action": "opened",
    "number": 42,
    "pull_request": {
        "number": 42,
        "title": "Add awesome feature",
        "user": {"login": "octocat"},
        "head": {"sha": "abc123"},
        "merged": False,
    },
    "repository": {"full_name": "octocat/hello-world"},
}


# ------------------------------------------------------------------ #
#  Tests                                                               #
# ------------------------------------------------------------------ #

def test_health(test_client):
    resp = test_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ping_event(test_client):
    payload = {"zen": "Keep it logically awesome.", "hook_id": 1}
    resp = post_event(test_client, "ping", payload)
    assert resp.status_code == 200
    assert resp.json()["message"] == "pong"
    assert resp.json()["zen"] == "Keep it logically awesome."


def test_invalid_signature_rejected(test_client, monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "real-secret")
    body = json.dumps(PR_PAYLOAD).encode()
    resp = test_client.post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=badsignature",
        },
    )
    assert resp.status_code == 401


def test_missing_signature_rejected(test_client, monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "real-secret")
    body = json.dumps(PR_PAYLOAD).encode()
    resp = test_client.post(
        "/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-GitHub-Event": "pull_request"},
    )
    assert resp.status_code == 401

def test_empty_body_returns_401(test_client):
    empty = b""
    resp = test_client.post(
        "/webhook",
        content=b"",
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=anything",
        },
    )
    assert resp.status_code == 401


def test_pr_opened(test_client):
    resp = post_event(test_client, "pull_request", PR_PAYLOAD)
    assert resp.status_code == 200
    assert "Welcomed" in resp.json()["message"]
    # Verify exact GitHub API calls
    mock_gh.post_pr_comment.assert_awaited_once()
    mock_gh.add_labels.assert_awaited_once_with("octocat/hello-world", 42, ["needs-review"])
    mock_gh.set_commit_status.assert_awaited_once()


def test_pr_opened_github_api_failure(test_client):
    mock_gh.post_pr_comment.side_effect = GitHubAPIError("POST", "/comments", 403, "Forbidden")
    resp = post_event(test_client, "pull_request", PR_PAYLOAD)
    assert resp.status_code == 502
    assert resp.json()["message"] == "Failed to call GitHub API"
    assert resp.json()["status_code"] == 403


def test_pr_merged(test_client):
    payload = {
        **PR_PAYLOAD,
        "action": "closed",
        "pull_request": {**PR_PAYLOAD["pull_request"], "merged": True},
    }
    resp = post_event(test_client, "pull_request", payload)
    assert resp.status_code == 200
    comment_body = mock_gh.post_pr_comment.call_args[0][2]
    assert "merged" in comment_body.lower()


def test_pr_closed_not_merged(test_client):
    payload = {
        **PR_PAYLOAD,
        "action": "closed",
        "pull_request": {**PR_PAYLOAD["pull_request"], "merged": False},
    }
    resp = post_event(test_client, "pull_request", payload)
    assert resp.status_code == 200
    comment_body = mock_gh.post_pr_comment.call_args[0][2]
    assert "reopen" in comment_body.lower()


def test_malformed_payload_returns_422(test_client):
    # pull_request key is missing — Pydantic should catch this
    resp = post_event(test_client, "pull_request", {"action": "opened"})
    assert resp.status_code == 422
    assert "errors" in resp.json()


def test_unknown_event(test_client):
    resp = post_event(test_client, "star", {"action": "created"})
    assert resp.status_code == 200
    assert "star" in resp.json()["message"]