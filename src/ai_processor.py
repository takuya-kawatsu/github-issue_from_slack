import json
import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache

from google import genai
from google.cloud import storage
from google.genai import types
from google.genai.errors import ServerError

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

SELECTOR_SYSTEM_PROMPT = """\
あなたはコードベースのファイル選択アシスタントです。
ユーザーのリクエスト内容に基づいて、GitHub Issue の作成に必要な関連ファイルを選択してください。

ルール:
- リクエストの実装・修正に直接関係するファイルを選ぶ
- 関連する型定義、設定ファイル、ルーティング定義なども含める
- CI/CD、テスト、ドキュメントなど明らかに無関係なファイルは除外する
- 迷ったら含める側に倒す（不足より過剰の方が安全）
"""

SELECTOR_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "selected_files": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
        ),
    },
    required=["selected_files"],
)

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


_FILE_HEADER_RE = re.compile(r"^## File: (.+)$", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"^```\w*\s*$", re.MULTILINE)

# Token budget safety margin: ~800K tokens ≈ ~3.2M chars (1 token ≈ 4 chars)
_MAX_CONTEXT_CHARS = 3_200_000

# --- Synopsis extraction patterns per language ---

# Go
_GO_IMPORT_RE = re.compile(r'^\s*"(.+)"', re.MULTILINE)
_GO_IMPORT_BLOCK_RE = re.compile(r"^import\s*\((.*?)\)", re.MULTILINE | re.DOTALL)
_GO_SINGLE_IMPORT_RE = re.compile(r'^import\s+"(.+)"', re.MULTILINE)
_GO_PACKAGE_RE = re.compile(r"^package\s+(\w+)", re.MULTILINE)
_GO_FUNC_RE = re.compile(
    r"^func\s+(?:\(\s*\w+\s+\*?(\w+)\s*\)\s+)?(\w+)\s*\(", re.MULTILINE
)
_GO_TYPE_RE = re.compile(r"^type\s+(\w+)\s+(struct|interface)", re.MULTILINE)

# TypeScript / TSX
_TS_IMPORT_RE = re.compile(r"^import\s+.*?from\s+['\"](.+?)['\"]", re.MULTILINE)
_TS_FUNC_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE
)
_TS_CONST_FUNC_RE = re.compile(
    r"^(?:export\s+)?const\s+(\w+)\s*(?::\s*\w+)?\s*=\s*(?:async\s*)?\(", re.MULTILINE
)
_TS_TYPE_RE = re.compile(
    r"^(?:export\s+)?(?:type|interface)\s+(\w+)", re.MULTILINE
)

# SQL
_SQL_CREATE_RE = re.compile(
    r"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|FUNCTION|INDEX|TYPE|TRIGGER)"
    r"\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)",
    re.MULTILINE | re.IGNORECASE,
)


def _extract_code_body(section: str) -> str:
    """セクションからコードフェンス内のソースコードを取り出す。"""
    fences = list(_CODE_FENCE_RE.finditer(section))
    if len(fences) >= 2:
        return section[fences[0].end() : fences[1].start()]
    return section


def _synopsis_go(code: str) -> dict:
    imports: list[str] = []
    block = _GO_IMPORT_BLOCK_RE.search(code)
    if block:
        imports = _GO_IMPORT_RE.findall(block.group(1))
    else:
        imports = _GO_SINGLE_IMPORT_RE.findall(code)

    pkg_match = _GO_PACKAGE_RE.search(code)
    package = pkg_match.group(1) if pkg_match else ""

    definitions: list[str] = []
    for m in _GO_TYPE_RE.finditer(code):
        definitions.append(f"{m.group(2)} {m.group(1)}")
    for m in _GO_FUNC_RE.finditer(code):
        receiver = m.group(1)
        name = m.group(2)
        if receiver:
            definitions.append(f"func ({receiver}).{name}()")
        else:
            definitions.append(f"func {name}()")

    return {"package": package, "imports": imports, "defines": definitions}


def _synopsis_ts(code: str) -> dict:
    imports = _TS_IMPORT_RE.findall(code)

    definitions: list[str] = []
    for m in _TS_TYPE_RE.finditer(code):
        definitions.append(f"type {m.group(1)}")
    for m in _TS_FUNC_RE.finditer(code):
        definitions.append(f"function {m.group(1)}")
    for m in _TS_CONST_FUNC_RE.finditer(code):
        definitions.append(f"const {m.group(1)}()")

    return {"imports": imports, "defines": definitions}


def _synopsis_sql(code: str) -> dict:
    objects = _SQL_CREATE_RE.findall(code)
    return {"creates": objects}


def _build_synopsis(file_path: str, section: str) -> str:
    """ファイルパスと内容からコンパクトなシノプシス文字列を生成する。"""
    code = _extract_code_body(section)
    ext = file_path.rsplit(".", 1)[-1] if "." in file_path else ""

    lines = [f"## {file_path}"]

    if ext == "go":
        info = _synopsis_go(code)
        if info["package"]:
            lines.append(f"package: {info['package']}")
        if info["imports"]:
            lines.append(f"imports: {', '.join(info['imports'])}")
        if info["defines"]:
            lines.append(f"defines: {', '.join(info['defines'])}")
    elif ext in ("ts", "tsx"):
        info = _synopsis_ts(code)
        if info["imports"]:
            lines.append(f"imports: {', '.join(info['imports'])}")
        if info["defines"]:
            lines.append(f"defines: {', '.join(info['defines'])}")
    elif ext == "sql":
        info = _synopsis_sql(code)
        if info["creates"]:
            lines.append(f"creates: {', '.join(info['creates'])}")
    elif ext in ("yml", "yaml", "json", "md"):
        # 設定/ドキュメント系はパスのみで十分
        pass

    return "\n".join(lines)


def _build_synopsis_index(sections: dict[str, str]) -> str:
    """全ファイルのシノプシスを結合したインデックス文字列を返す。"""
    parts = [_build_synopsis(path, content) for path, content in sections.items()]
    index = "\n\n".join(parts)
    logger.info("Synopsis index: %d files, %d chars", len(parts), len(index))
    return index


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


def _parse_context_sections(context: str) -> dict[str, str]:
    """コンテキストを `## File:` ヘッダーで分割し {パス: 内容} の辞書を返す。"""
    sections: dict[str, str] = {}
    matches = list(_FILE_HEADER_RE.finditer(context))
    for i, match in enumerate(matches):
        file_path = match.group(1).strip()
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(context)
        sections[file_path] = context[start:end]
    return sections


def _select_relevant_files(
    synopsis_index: str, user_request: str
) -> list[str]:
    """Gemini Flash でシノプシスを基にユーザーリクエストに関連するファイルを選択する。"""
    config = get_config()
    client = genai.Client(
        api_key=config.gemini_api_key,
        http_options=types.HttpOptions(timeout=_HTTP_TIMEOUT_MS),
    )

    contents = (
        f"# コードベースのシノプシス\n\n{synopsis_index}\n\n"
        f"# ユーザーリクエスト\n\n{user_request}"
    )

    response = client.models.generate_content(
        model=config.gemini_selector_model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SELECTOR_SYSTEM_PROMPT,
            temperature=0.0,
            max_output_tokens=65536,
            response_mime_type="application/json",
            response_schema=SELECTOR_RESPONSE_SCHEMA,
        ),
    )

    data = json.loads(response.text)

    selected = data.get("selected_files", [])
    logger.info("Context selector: %d files selected", len(selected))
    return selected


def _build_filtered_context(
    sections: dict[str, str], selected_files: list[str]
) -> str:
    """選択されたファイルのコンテキストを結合する。トークン予算を超えた場合は切り詰める。"""
    parts: list[str] = []
    total_chars = 0
    for file_path in selected_files:
        section = sections.get(file_path)
        if not section:
            continue
        if total_chars + len(section) > _MAX_CONTEXT_CHARS:
            logger.warning(
                "Context budget exceeded at %d chars, truncating remaining files",
                total_chars,
            )
            break
        parts.append(section)
        total_chars += len(section)
    logger.info("Filtered context: %d files, %d chars", len(parts), total_chars)
    return "\n".join(parts)


_MAX_RETRIES = 3
_INITIAL_BACKOFF_SEC = 5
_HTTP_TIMEOUT_MS = 600_000  # 10 minutes


def structurize(text: str) -> StructuredIssue:
    config = get_config()
    client = genai.Client(
        api_key=config.gemini_api_key,
        http_options=types.HttpOptions(timeout=_HTTP_TIMEOUT_MS),
    )

    codebase_context = _load_codebase_context()

    if codebase_context:
        sections = _parse_context_sections(codebase_context)
        if sections:
            synopsis_index = _build_synopsis_index(sections)
            selected = _select_relevant_files(synopsis_index, text)
            filtered = _build_filtered_context(sections, selected)
        else:
            filtered = codebase_context

        contents = [
            types.Part.from_text(text=f"# Codebase Context\n\n{filtered}"),
            types.Part.from_text(text=f"# User Request\n\n{text}"),
        ]
    else:
        contents = text

    generate_config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.2,
        max_output_tokens=8192,
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
    )

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=config.gemini_model,
                contents=contents,
                config=generate_config,
            )
            break
        except ServerError as exc:
            last_error = exc
            if attempt < _MAX_RETRIES - 1:
                wait = _INITIAL_BACKOFF_SEC * (2 ** attempt)
                logger.warning(
                    "Gemini API ServerError (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, _MAX_RETRIES, wait, exc,
                )
                time.sleep(wait)
            else:
                logger.error("Gemini API failed after %d attempts", _MAX_RETRIES)
                raise last_error

    data = json.loads(response.text)
    logger.info("Structured issue: %s", data.get("title"))

    return StructuredIssue(
        title=data["title"],
        body=data["body"],
        labels=data.get("labels", []),
    )
