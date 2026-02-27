from unittest.mock import MagicMock, patch

from slack_sdk.errors import SlackApiError

from src.handlers import (
    MENTION_PATTERN,
    _build_preview_blocks,
    _build_result_blocks,
    _extract_issue_from_metadata,
    _is_approver,
    _truncate,
)


# --- _truncate ---


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello", 10) == "hello"

    def test_exact_limit_unchanged(self):
        assert _truncate("12345", 5) == "12345"

    def test_over_limit_truncated(self):
        result = _truncate("1234567890", 8)
        assert result == "12345..."
        assert len(result) == 8

    def test_empty_string(self):
        assert _truncate("", 10) == ""


# --- _build_preview_blocks ---


class TestBuildPreviewBlocks:
    def test_block_structure(self):
        blocks = _build_preview_blocks("Title", "Body", ["bug"])
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "section"
        assert blocks[2]["type"] == "section"
        assert blocks[3]["type"] == "section"
        assert blocks[4]["type"] == "divider"
        assert blocks[5]["type"] == "actions"

    def test_action_ids(self):
        blocks = _build_preview_blocks("T", "B", [])
        actions = blocks[5]["elements"]
        assert actions[0]["action_id"] == "issue_create"
        assert actions[1]["action_id"] == "issue_cancel"

    def test_labels_present(self):
        blocks = _build_preview_blocks("T", "B", ["bug", "enhancement"])
        label_block = blocks[3]
        assert "`bug`" in label_block["text"]["text"]
        assert "`enhancement`" in label_block["text"]["text"]

    def test_labels_empty(self):
        blocks = _build_preview_blocks("T", "B", [])
        label_block = blocks[3]
        assert "なし" in label_block["text"]["text"]

    def test_long_body_truncated(self):
        long_body = "x" * 4000
        blocks = _build_preview_blocks("T", long_body, [])
        body_text = blocks[2]["text"]["text"]
        assert len(body_text) <= 3000


# --- _build_result_blocks ---


class TestBuildResultBlocks:
    def test_single_section(self):
        blocks = _build_result_blocks("done")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "section"
        assert blocks[0]["text"]["text"] == "done"


# --- _is_approver ---


class TestIsApprover:
    @patch("src.handlers.get_config")
    def test_no_group_configured_allows_everyone(self, mock_config):
        mock_config.return_value = MagicMock(approver_slack_group="")
        client = MagicMock()
        assert _is_approver(client, "U123") is True
        client.usergroups_users_list.assert_not_called()

    @patch("src.handlers.get_config")
    def test_user_in_group(self, mock_config):
        mock_config.return_value = MagicMock(approver_slack_group="S_GROUP")
        client = MagicMock()
        client.usergroups_users_list.return_value = {"users": ["U123", "U456"]}
        assert _is_approver(client, "U123") is True

    @patch("src.handlers.get_config")
    def test_user_not_in_group(self, mock_config):
        mock_config.return_value = MagicMock(approver_slack_group="S_GROUP")
        client = MagicMock()
        client.usergroups_users_list.return_value = {"users": ["U456"]}
        assert _is_approver(client, "U123") is False

    @patch("src.handlers.get_config")
    def test_api_error_returns_false(self, mock_config):
        mock_config.return_value = MagicMock(approver_slack_group="S_GROUP")
        client = MagicMock()
        client.usergroups_users_list.side_effect = SlackApiError(
            message="error", response=MagicMock(status_code=500)
        )
        assert _is_approver(client, "U123") is False


# --- _extract_issue_from_metadata ---


class TestExtractIssueFromMetadata:
    def test_direct_metadata(self):
        payload = {"title": "T", "body": "B", "labels": ["bug"]}
        body = {"message": {"metadata": {"event_payload": payload}}}
        assert _extract_issue_from_metadata(body) == payload

    def test_payload_null_returns_none(self):
        body = {"message": {"metadata": {"event_payload": None}}}
        assert _extract_issue_from_metadata(body) is None

    def test_no_metadata_returns_none(self):
        body = {"message": {}}
        assert _extract_issue_from_metadata(body) is None

    def test_no_message_returns_none(self):
        assert _extract_issue_from_metadata({}) is None


# --- MENTION_PATTERN ---


class TestMentionPattern:
    def test_removes_bot_mention(self):
        text = "<@U1234ABC> ログインで500エラーが出ます"
        cleaned = MENTION_PATTERN.sub("", text).strip()
        assert cleaned == "ログインで500エラーが出ます"

    def test_removes_multiple_mentions(self):
        text = "<@U1234ABC> <@U5678DEF> テスト"
        cleaned = MENTION_PATTERN.sub("", text).strip()
        assert cleaned == "テスト"
