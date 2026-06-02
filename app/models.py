from pydantic import BaseModel


# ------------------------------------------------------------------ #
#  Shared sub-models                                                   #
# ------------------------------------------------------------------ #

class GitHubUser(BaseModel):
    login: str


class HeadRef(BaseModel):
    sha: str


class Repository(BaseModel):
    full_name: str

class Installation(BaseModel):
    id: int


# ------------------------------------------------------------------ #
#  Pull request                                                        #
# ------------------------------------------------------------------ #

class PullRequest(BaseModel):
    number: int
    title: str
    user: GitHubUser
    head: HeadRef
    merged: bool = False


class PRPayload(BaseModel):
    action: str
    pull_request: PullRequest
    repository: Repository
    installation: Installation
    requested_reviewer: GitHubUser | None = None


# ------------------------------------------------------------------ #
#  Push                                                                #
# ------------------------------------------------------------------ #

class Pusher(BaseModel):
    name: str


class PushPayload(BaseModel):
    ref: str
    repository: Repository
    pusher: Pusher
    installation: Installation | None = None
    commits: list[dict] = []


# ------------------------------------------------------------------ #
#  Ping                                                                #
# ------------------------------------------------------------------ #

class PingPayload(BaseModel):
    zen: str = ""
    hook_id: int = 0