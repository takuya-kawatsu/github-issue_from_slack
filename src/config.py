import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Config:
    slack_bot_token: str
    slack_signing_secret: str
    github_token: str
    github_repo: str
    gemini_api_key: str
    gcp_project_id: str
    gemini_model: str = "gemini-2.5-pro"
    gemini_selector_model: str = "gemini-2.5-flash"
    context_gcs_bucket: str = ""
    context_gcs_path: str = "llm_context.md"
    approver_slack_group: str = ""


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config(
        slack_bot_token=os.environ["SLACK_BOT_TOKEN"],
        slack_signing_secret=os.environ["SLACK_SIGNING_SECRET"],
        github_token=os.environ["GITHUB_TOKEN"],
        github_repo=os.environ["GITHUB_REPO"],
        gemini_api_key=os.environ["GEMINI_API_KEY"],
        gcp_project_id=os.environ["GCP_PROJECT_ID"],
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-pro"),
        gemini_selector_model=os.environ.get("GEMINI_SELECTOR_MODEL", "gemini-2.5-flash"),
        context_gcs_bucket=os.environ.get("CONTEXT_GCS_BUCKET", ""),
        context_gcs_path=os.environ.get("CONTEXT_GCS_PATH", "llm_context.md"),
        approver_slack_group=os.environ.get("APPROVER_SLACK_GROUP", ""),
    )
