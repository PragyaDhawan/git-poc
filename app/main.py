import hmac
import json
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import settings
from app.github_client import GitHubClient
from app.handlers import handle_pull_request, handle_push, handle_ping
from app.models import PRPayload, PushPayload, PingPayload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Lifespan: create and cleanly close the shared GitHub client                #
# --------------------------------------------------------------------------- #

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.github_client = GitHubClient(
        app_id=settings.GITHUB_APP_ID,
        installation_id=settings.GITHUB_INSTALLATION_ID,
        private_key_path=settings.GITHUB_PRIVATE_KEY_PATH,
    )
    logger.info("GitHub client initialised")
    yield
    await app.state.github_client.close()
    logger.info("GitHub client closed")


app = FastAPI(
    title="GitHub Webhook Receiver",
    description="Listens to GitHub events via webhooks and responds back",
    version="1.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
#  Dependencies                                                                #
# --------------------------------------------------------------------------- #

def get_github_client(request: Request) -> GitHubClient:
    """Inject the shared GitHubClient from app state."""
    return request.app.state.github_client


# --------------------------------------------------------------------------- #
#  Signature verification                                                      #
# --------------------------------------------------------------------------- #

def verify_signature(payload: bytes, signature_header: str | None, secret: str) -> None:
    """Verify the GitHub webhook HMAC-SHA256 signature."""
    if not secret:
        logger.warning("WEBHOOK_SECRET not set — skipping signature verification")
        return

    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Hub-Signature-256 header",
        )

    expected = "sha256=" + hmac.digest(
        secret.encode(), payload, "sha256"
    ).hex()

    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )


# --------------------------------------------------------------------------- #
#  Routes                                                                      #
# --------------------------------------------------------------------------- #

@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
    client: GitHubClient = Depends(get_github_client),
):
    raw_body = await request.body()
    verify_signature(raw_body, x_hub_signature_256, settings.WEBHOOK_SECRET)

    event = x_github_event or "unknown"

    try:
        raw = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or empty JSON payload",
        )

    try:
        match event:
            case "ping":
                return await handle_ping(PingPayload(**raw))
            case "pull_request":
                return await handle_pull_request(PRPayload(**raw), client)
            case "push":
                return await handle_push(PushPayload(**raw), client)
            case _:
                logger.info("Unhandled event type: %s", event)
                return JSONResponse(
                    content={"message": f"Event '{event}' received but not handled"}
                )
    except ValidationError as exc:
        logger.warning("Payload validation failed for event '%s': %s", event, exc)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"message": "Unexpected payload shape", "errors": exc.errors()},
        )