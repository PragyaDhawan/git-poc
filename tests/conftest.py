import os

# Must be set before app is imported so GitHubClient.__init__ doesn't raise.
# The real client is never called in tests — get_github_client is overridden.
os.environ.setdefault("GITHUB_TOKEN", "test-token-not-used")
os.environ.setdefault("WEBHOOK_SECRET", "test-secret")