import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Config:
    slack_bot_token: str
    slack_signing_secret: str
    github_token: str
    github_repo: str
    gcp_project_id: str
    gcp_location: str = "asia-northeast1"
    gemini_model: str = "gemini-2.5-pro"
    context_gcs_bucket: str = ""
    context_gcs_path: str = "llm_context.md"


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config(
        slack_bot_token=os.environ["SLACK_BOT_TOKEN"],
        slack_signing_secret=os.environ["SLACK_SIGNING_SECRET"],
        github_token=os.environ["GITHUB_TOKEN"],
        github_repo=os.environ["GITHUB_REPO"],
        gcp_project_id=os.environ["GCP_PROJECT_ID"],
        gcp_location=os.environ.get("GCP_LOCATION", "asia-northeast1"),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-pro"),
        context_gcs_bucket=os.environ.get("CONTEXT_GCS_BUCKET", ""),
        context_gcs_path=os.environ.get("CONTEXT_GCS_PATH", "llm_context.md"),
    )
