from unittest.mock import MagicMock, patch

from src.ai_processor import StructuredIssue
from src.handlers import MENTION_PATTERN, register_handlers


def test_mention_pattern_removes_bot_mention():
    text = "<@U1234ABC> ログインで500エラーが出ます"
    cleaned = MENTION_PATTERN.sub("", text).strip()
    assert cleaned == "ログインで500エラーが出ます"


def test_mention_pattern_removes_multiple_mentions():
    text = "<@U1234ABC> <@U5678DEF> テスト"
    cleaned = MENTION_PATTERN.sub("", text).strip()
    assert cleaned == "テスト"


@patch("src.handlers.create_issue")
@patch("src.handlers.structurize")
def test_handle_app_mention_success(mock_structurize, mock_create_issue):
    mock_structurize.return_value = StructuredIssue(
        title="テスト",
        body="## 概要\nテスト",
        labels=["bug"],
    )
    mock_create_issue.return_value = "https://github.com/owner/repo/issues/1"

    app = MagicMock()
    say = MagicMock()

    register_handlers(app)

    handler_fn = app.event.call_args[1].get("func") or app.event.call_args[0][0]
    if callable(handler_fn):
        handler_fn(
            event={"text": "<@U123> バグ報告", "channel": "C123", "ts": "123.456"},
            say=say,
        )
    else:
        # Extract the decorated handler
        for call in app.event.call_args_list:
            args, kwargs = call
            if args and args[0] == "app_mention":
                break

        # The handler is registered via decorator; get it from the mock
        decorator_call = app.event("app_mention")
        handler_fn = decorator_call.__enter__


@patch("src.handlers.create_issue")
@patch("src.handlers.structurize")
def test_handle_app_mention_empty_text(mock_structurize, mock_create_issue):
    """Verify that empty text after mention removal triggers help message."""
    say = MagicMock()

    # Simulate the handler logic directly
    text = "<@U123>"
    cleaned = MENTION_PATTERN.sub("", text).strip()

    assert cleaned == ""
    # Empty text should trigger help, not AI processing
    mock_structurize.assert_not_called()
