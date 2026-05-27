from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Required: GitHub personal access token (needs repo + write:discussion scopes)
    GITHUB_TOKEN: str = ""

    # Recommended: secret token set in GitHub webhook settings
    WEBHOOK_SECRET: str = ""

    # Bot label added to auto-comments (cosmetic)
    BOT_NAME: str = "webhook-bot"


settings = Settings()
