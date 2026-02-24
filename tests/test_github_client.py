from unittest.mock import MagicMock, patch

from github import GithubException

from src.ai_processor import StructuredIssue
from src.github_client import _filter_valid_labels, create_issue


@patch("src.github_client.get_config")
@patch("src.github_client.Github")
def test_create_issue_success(mock_github_cls, mock_config):
    mock_config.return_value = MagicMock(
        github_token="fake-token",
        github_repo="owner/repo",
    )

    mock_label_bug = MagicMock()
    mock_label_bug.name = "bug"
    mock_label_enhancement = MagicMock()
    mock_label_enhancement.name = "enhancement"

    mock_repo = MagicMock()
    mock_repo.get_labels.return_value = [mock_label_bug, mock_label_enhancement]

    mock_created_issue = MagicMock()
    mock_created_issue.number = 42
    mock_created_issue.html_url = "https://github.com/owner/repo/issues/42"
    mock_repo.create_issue.return_value = mock_created_issue

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo
    mock_github_cls.return_value = mock_gh

    issue = StructuredIssue(
        title="テスト Issue",
        body="## 概要\nテスト",
        labels=["bug"],
    )

    url = create_issue(issue)

    assert url == "https://github.com/owner/repo/issues/42"
    mock_repo.create_issue.assert_called_once_with(
        title="テスト Issue",
        body="## 概要\nテスト",
        labels=["bug"],
    )


def test_filter_valid_labels():
    mock_label_bug = MagicMock()
    mock_label_bug.name = "bug"
    mock_label_enhancement = MagicMock()
    mock_label_enhancement.name = "enhancement"

    mock_repo = MagicMock()
    mock_repo.get_labels.return_value = [mock_label_bug, mock_label_enhancement]

    result = _filter_valid_labels(mock_repo, ["bug", "nonexistent", "Enhancement"])

    assert "bug" in result
    assert "enhancement" in result
    assert len(result) == 2


def test_filter_valid_labels_on_api_error():
    mock_repo = MagicMock()
    mock_repo.get_labels.side_effect = GithubException(500, "error", None)

    result = _filter_valid_labels(mock_repo, ["bug"])

    assert result == []
