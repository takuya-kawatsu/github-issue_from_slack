import json
from unittest.mock import MagicMock, patch

from src.ai_processor import StructuredIssue, structurize


@patch("src.ai_processor._load_codebase_context")
@patch("src.ai_processor.get_config")
@patch("src.ai_processor.genai.Client")
def test_structurize_returns_structured_issue(
    mock_client_cls, mock_config, mock_load_context
):
    mock_config.return_value = MagicMock(
        gemini_api_key="test-api-key",
        gcp_project_id="test-project",
        gemini_model="gemini-2.5-pro",
    )
    mock_load_context.return_value = "# Codebase\nsome context"

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "title": "ログイン画面で500エラー",
        "body": "## 概要\nログイン時にサーバーエラーが発生する",
        "labels": ["bug"],
    })

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    mock_client_cls.return_value = mock_client

    result = structurize("ログイン画面で500エラーが発生します")

    assert isinstance(result, StructuredIssue)
    assert result.title == "ログイン画面で500エラー"
    assert "概要" in result.body
    assert result.labels == ["bug"]

    mock_client.models.generate_content.assert_called_once()


@patch("src.ai_processor._load_codebase_context")
@patch("src.ai_processor.get_config")
@patch("src.ai_processor.genai.Client")
def test_structurize_with_empty_labels(mock_client_cls, mock_config, mock_load_context):
    mock_config.return_value = MagicMock(
        gemini_api_key="test-api-key",
        gcp_project_id="test-project",
        gemini_model="gemini-2.5-pro",
    )
    mock_load_context.return_value = ""

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "title": "機能要望",
        "body": "## 概要\n新機能の提案",
    })

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    mock_client_cls.return_value = mock_client

    result = structurize("新しい機能を追加してほしい")

    assert result.labels == []


@patch("src.ai_processor._load_codebase_context")
@patch("src.ai_processor.get_config")
@patch("src.ai_processor.genai.Client")
def test_structurize_without_context_sends_text_directly(
    mock_client_cls, mock_config, mock_load_context
):
    mock_config.return_value = MagicMock(
        gemini_api_key="test-api-key",
        gcp_project_id="test-project",
        gemini_model="gemini-2.5-pro",
    )
    mock_load_context.return_value = ""

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "title": "テスト",
        "body": "## 概要\nテスト",
        "labels": ["enhancement"],
    })

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    mock_client_cls.return_value = mock_client

    structurize("テストメッセージ")

    call_args = mock_client.models.generate_content.call_args
    assert call_args.kwargs["contents"] == "テストメッセージ"


@patch("src.ai_processor._load_codebase_context")
@patch("src.ai_processor.get_config")
@patch("src.ai_processor.genai.Client")
def test_structurize_with_context_sends_parts(
    mock_client_cls, mock_config, mock_load_context
):
    mock_config.return_value = MagicMock(
        gemini_api_key="test-api-key",
        gcp_project_id="test-project",
        gemini_model="gemini-2.5-pro",
    )
    mock_load_context.return_value = "# Codebase\nsome context"

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "title": "テスト",
        "body": "## 概要\nテスト",
        "labels": ["enhancement"],
    })

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    mock_client_cls.return_value = mock_client

    structurize("テストメッセージ")

    call_args = mock_client.models.generate_content.call_args
    contents = call_args.kwargs["contents"]
    assert isinstance(contents, list)
    assert len(contents) == 2
