from unittest.mock import MagicMock, patch

from slack_sdk.errors import SlackApiError

from src.handlers import (
    FIRESTORE_COLLECTION,
    MENTION_PATTERN,
    _build_preview_blocks,
    _build_result_blocks,
    _delete_issue_data,
    _extract_issue_data,
    _is_approver,
    _load_issue_data,
    _save_issue_data,
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


# --- Firestore Issue Data ---


class TestFirestoreIssueData:
    @patch("src.handlers._get_firestore_client")
    def test_save_and_load_roundtrip(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # save
        saved_data = {}

        def capture_set(data):
            saved_data.update(data)

        mock_doc = MagicMock()
        mock_doc.set = capture_set
        mock_client.collection.return_value.document.return_value = mock_doc

        doc_id = _save_issue_data("Title", "Body", ["bug"])
        assert isinstance(doc_id, str)
        assert len(doc_id) == 36  # UUID format
        assert saved_data["title"] == "Title"
        assert saved_data["body"] == "Body"
        assert saved_data["labels"] == ["bug"]
        assert "expire_at" in saved_data

        # load
        mock_doc_snapshot = MagicMock()
        mock_doc_snapshot.exists = True
        mock_doc_snapshot.to_dict.return_value = {
            "title": "Title",
            "body": "Body",
            "labels": ["bug"],
            "expire_at": saved_data["expire_at"],
        }
        mock_client.collection.return_value.document.return_value.get.return_value = (
            mock_doc_snapshot
        )

        result = _load_issue_data(doc_id)
        assert result == {"title": "Title", "body": "Body", "labels": ["bug"]}

    @patch("src.handlers._get_firestore_client")
    def test_load_nonexistent_document_returns_none(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_doc_snapshot = MagicMock()
        mock_doc_snapshot.exists = False
        mock_client.collection.return_value.document.return_value.get.return_value = (
            mock_doc_snapshot
        )

        assert _load_issue_data("nonexistent-id") is None

    @patch("src.handlers._get_firestore_client")
    def test_load_exception_returns_none(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.collection.return_value.document.return_value.get.side_effect = (
            Exception("Firestore error")
        )

        assert _load_issue_data("some-id") is None

    @patch("src.handlers._get_firestore_client")
    def test_delete_calls_firestore(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        _delete_issue_data("doc-123")

        mock_client.collection.assert_called_with(FIRESTORE_COLLECTION)
        mock_client.collection.return_value.document.assert_called_with("doc-123")
        mock_client.collection.return_value.document.return_value.delete.assert_called_once()

    @patch("src.handlers._get_firestore_client")
    def test_delete_exception_does_not_raise(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.collection.return_value.document.return_value.delete.side_effect = (
            Exception("Firestore error")
        )

        # Should not raise
        _delete_issue_data("doc-123")


# --- _build_preview_blocks ---


class TestBuildPreviewBlocks:
    @patch("src.handlers._save_issue_data", return_value="mock-uuid")
    def test_block_structure(self, mock_save):
        blocks = _build_preview_blocks("Title", "Body", ["bug"])
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "section"
        assert blocks[2]["type"] == "section"
        assert blocks[3]["type"] == "section"
        assert blocks[4]["type"] == "divider"
        assert blocks[5]["type"] == "actions"

    @patch("src.handlers._save_issue_data", return_value="mock-uuid")
    def test_action_ids(self, mock_save):
        blocks = _build_preview_blocks("T", "B", [])
        actions = blocks[5]["elements"]
        assert actions[0]["action_id"] == "issue_create"
        assert actions[1]["action_id"] == "issue_cancel"

    @patch("src.handlers._save_issue_data", return_value="mock-uuid")
    def test_create_button_has_uuid_value(self, mock_save):
        blocks = _build_preview_blocks("T", "B", ["bug"])
        create_btn = blocks[5]["elements"][0]
        assert create_btn["value"] == "mock-uuid"

    @patch("src.handlers._save_issue_data", return_value="mock-uuid")
    def test_cancel_button_has_uuid_value(self, mock_save):
        blocks = _build_preview_blocks("T", "B", ["bug"])
        cancel_btn = blocks[5]["elements"][1]
        assert cancel_btn["value"] == "mock-uuid"

    @patch("src.handlers._save_issue_data", return_value="mock-uuid")
    def test_labels_present(self, mock_save):
        blocks = _build_preview_blocks("T", "B", ["bug", "enhancement"])
        label_block = blocks[3]
        assert "`bug`" in label_block["text"]["text"]
        assert "`enhancement`" in label_block["text"]["text"]

    @patch("src.handlers._save_issue_data", return_value="mock-uuid")
    def test_labels_empty(self, mock_save):
        blocks = _build_preview_blocks("T", "B", [])
        label_block = blocks[3]
        assert "なし" in label_block["text"]["text"]

    @patch("src.handlers._save_issue_data", return_value="mock-uuid")
    def test_long_body_truncated(self, mock_save):
        long_body = "x" * 4000
        blocks = _build_preview_blocks("T", long_body, [])
        body_text = blocks[2]["text"]["text"]
        assert len(body_text) <= 3000

    @patch("src.handlers._save_issue_data", return_value="mock-uuid")
    def test_no_mention_block_in_preview(self, mock_save):
        blocks = _build_preview_blocks("T", "B", [])
        assert blocks[0]["type"] == "header"


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


# --- _extract_issue_data ---


class TestExtractIssueData:
    @patch("src.handlers._load_issue_data")
    def test_extract_from_action_value(self, mock_load):
        mock_load.return_value = {"title": "T", "body": "B", "labels": ["bug"]}
        body = {"actions": [{"action_id": "issue_create", "value": "some-uuid"}]}
        result = _extract_issue_data(body)
        assert result["title"] == "T"
        assert result["body"] == "B"
        assert result["labels"] == ["bug"]
        mock_load.assert_called_once_with("some-uuid")

    def test_no_actions_returns_none(self):
        assert _extract_issue_data({"actions": []}) is None

    def test_no_value_returns_none(self):
        body = {"actions": [{"action_id": "issue_create"}]}
        assert _extract_issue_data(body) is None

    @patch("src.handlers._load_issue_data", return_value=None)
    def test_invalid_value_returns_none(self, mock_load):
        body = {"actions": [{"action_id": "issue_create", "value": "bad-id"}]}
        assert _extract_issue_data(body) is None


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
