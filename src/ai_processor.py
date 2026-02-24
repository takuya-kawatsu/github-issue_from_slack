import json
import logging
from dataclasses import dataclass

from google import genai
from google.genai import types

from src.config import get_config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
あなたはSlackメッセージをGitHub Issueに変換するアシスタントです。
ユーザーから受け取ったテキストを分析し、構造化されたGitHub Issueを作成してください。

以下のルールに従ってください:
- titleは簡潔で内容を的確に表す日本語のタイトル（50文字以内）
- bodyはMarkdown形式で以下のセクションを含める:
  ## 📝 背景と目的 / Context & Goal
  - 

  ## 🎯 期待される結果 / Expected Outcome
  - [ ] 

  ## 🛠️ 制約や条件
  - 

- labelsは内容に応じて適切なものを選択（例: bug, enhancement, documentation, question）
- 元のメッセージの意図を正確に反映すること
- 情報が不足している場合でも、与えられた情報から最善のIssueを作成すること
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


def structurize(text: str) -> StructuredIssue:
    config = get_config()
    client = genai.Client(
        vertexai=True,
        project=config.gcp_project_id,
        location=config.gcp_location,
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
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
