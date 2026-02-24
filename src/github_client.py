import logging

from github import Github, GithubException

from src.ai_processor import StructuredIssue
from src.config import get_config

logger = logging.getLogger(__name__)


def create_issue(issue: StructuredIssue) -> str:
    config = get_config()
    gh = Github(config.github_token)
    repo = gh.get_repo(config.github_repo)

    valid_labels = _filter_valid_labels(repo, issue.labels)

    created = repo.create_issue(
        title=issue.title,
        body=issue.body,
        labels=valid_labels,
    )
    logger.info("Created issue #%d: %s", created.number, created.html_url)
    return created.html_url


def _filter_valid_labels(repo, requested_labels: list[str]) -> list[str]:
    try:
        existing = {label.name.lower(): label.name for label in repo.get_labels()}
    except GithubException:
        logger.warning("Failed to fetch labels, skipping label assignment")
        return []

    valid = []
    for label in requested_labels:
        matched = existing.get(label.lower())
        if matched:
            valid.append(matched)
        else:
            logger.info("Label '%s' does not exist in repo, skipping", label)
    return valid
