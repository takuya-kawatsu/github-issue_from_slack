import json
import logging
from dataclasses import dataclass
from functools import lru_cache

from google import genai
from google.cloud import storage
from google.genai import types

from src.config import get_config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
あなたはSlackメッセージをGitHub Issueに変換するアシスタントです。
コードベースの全体像が「Codebase Context」として提供されています。
ユーザーのリクエストを分析し、コードベースの知識を活用して、
具体的で実装可能なGitHub Issueを作成してください。

以下のルールに従ってください:
- titleは簡潔で内容を的確に表す日本語のタイトル（50文字以内）
- bodyはMarkdown形式で以下のセクションを含める:

  ## 📝 背景と目的 / Context & Goal
  - ユーザーのリクエストの背景と目的を簡潔に説明

  ## 🎯 受入条件 / Acceptance Criteria
  - [ ] ユーザーから見て何がどう変わるかを「〜できる」「〜が表示される」の形式で
  - [ ] エッジケース（エラー時、データなし時など）のふるまいも含める

  ## 🛠️ 制約や条件 / Constraints & Edge Cases
  - 技術的制約、エッジケース、注意点

- labelsは内容に応じて適切なものを選択（例: bug, enhancement, documentation, question）
- 元のメッセージの意図を正確に反映すること
- 情報が不足している場合でも、与えられた情報から最善のIssueを作成すること
- 非エンジニアの曖昧な表現でも、コードベースから該当箇所を特定すること
- 暗黙的な要件も補完すること
- 実装方法（how）ではなく、ユーザー体験の変化（what）を中心に記述すること
- 出力は簡潔に。長文の解説は不要
"""

RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "title": types.Schema(type=types.Type.STRING),
        "body": types.Schema(type=types.Type.STRING),
        "labels": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
        ),
    },
    required=["title", "body", "labels"],
)


@dataclass
class StructuredIssue:
    title: str
    body: str
    labels: list[str]


@lru_cache(maxsize=1)
def _load_codebase_context() -> str:
    config = get_config()
    if not config.context_gcs_bucket:
        logger.warning("CONTEXT_GCS_BUCKET not set, running without codebase context")
        return ""
    client = storage.Client(project=config.gcp_project_id)
    bucket = client.bucket(config.context_gcs_bucket)
    blob = bucket.blob(config.context_gcs_path)
    content = blob.download_as_text(encoding="utf-8")
    logger.info("Loaded codebase context: %d chars", len(content))
    return content


def structurize(text: str) -> StructuredIssue:
    config = get_config()
    client = genai.Client(
        vertexai=True,
        project=config.gcp_project_id,
        location=config.gcp_location,
    )

    codebase_context = _load_codebase_context()

    if codebase_context:
        contents = [
            types.Part.from_text(text=f"# Codebase Context\n\n{codebase_context}"),
            types.Part.from_text(text=f"# User Request\n\n{text}"),
        ]
    else:
        contents = text

    response = client.models.generate_content(
        model=config.gemini_model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
            max_output_tokens=8192,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )

    data = json.loads(response.text)
    logger.info("Structured issue: %s", data.get("title"))

    return StructuredIssue(
        title=data["title"],
        body=data["body"],
        labels=data.get("labels", []),
    )
