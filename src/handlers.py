import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from google.cloud import firestore
from slack_bolt import App
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.ai_processor import StructuredIssue, structurize
from src.config import get_config
from src.github_client import create_issue

logger = logging.getLogger(__name__)

MENTION_PATTERN = re.compile(r"<@[A-Z0-9]+>")

PREVIEW_BODY_LIMIT = 3000
FIRESTORE_COLLECTION = "pending_issues"
ISSUE_DATA_TTL_HOURS = 24


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


@lru_cache(maxsize=1)
def _get_firestore_client() -> firestore.Client:
    return firestore.Client()


def _save_issue_data(title: str, body: str, labels: list[str]) -> str:
    """Issue データを Firestore に保存し、ドキュメント ID を返す。"""
    doc_id = str(uuid.uuid4())
    client = _get_firestore_client()
    client.collection(FIRESTORE_COLLECTION).document(doc_id).set(
        {
            "title": title,
            "body": body,
            "labels": labels,
            "expire_at": datetime.now(timezone.utc) + timedelta(hours=ISSUE_DATA_TTL_HOURS),
        }
    )
    return doc_id


def _load_issue_data(doc_id: str) -> dict | None:
    """Firestore からドキュメントを取得する。存在しない/例外時は None。"""
    try:
        client = _get_firestore_client()
        doc = client.collection(FIRESTORE_COLLECTION).document(doc_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict()
        return {"title": data["title"], "body": data["body"], "labels": data["labels"]}
    except Exception:
        logger.exception("Failed to load issue data from Firestore")
        return None


def _delete_issue_data(doc_id: str) -> None:
    """Firestore ドキュメントを削除する。失敗はログのみ。"""
    try:
        client = _get_firestore_client()
        client.collection(FIRESTORE_COLLECTION).document(doc_id).delete()
    except Exception:
        logger.exception("Failed to delete issue data from Firestore")


def _build_preview_blocks(
    title: str, body: str, labels: list[str]
) -> list[dict]:
    label_text = ", ".join(f"`{label}`" for label in labels) if labels else "なし"
    doc_id = _save_issue_data(title, body, labels)
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Issue プレビュー", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*タイトル*\n{title}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate(f"*本文*\n{body}", PREVIEW_BODY_LIMIT),
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*ラベル*: {label_text}"},
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "作成"},
                    "style": "primary",
                    "action_id": "issue_create",
                    "value": doc_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "キャンセル"},
                    "style": "danger",
                    "action_id": "issue_cancel",
                    "value": doc_id,
                },
            ],
        },
    ]
    return blocks


def _build_result_blocks(text: str) -> list[dict]:
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }
    ]


def _is_approver(client: WebClient, user_id: str) -> bool:
    config = get_config()
    group_id = config.approver_slack_group
    if not group_id:
        return True
    try:
        resp = client.usergroups_users_list(usergroup=group_id)
        members = resp.get("users", [])
        return user_id in members
    except SlackApiError:
        logger.exception("Failed to fetch usergroup members")
        return False


def _extract_issue_data(body: dict) -> dict | None:
    """アクション payload から Issue データを取得する。
    ボタンの value (Firestore ドキュメント ID) から取得する。
    """
    actions = body.get("actions", [])
    for action in actions:
        value = action.get("value")
        if value:
            data = _load_issue_data(value)
            if data:
                return data
    return None


def register_handlers(app: App) -> None:
    @app.event("app_mention")
    def handle_app_mention(event, say, client):
        text = event.get("text", "")
        channel = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")

        text = MENTION_PATTERN.sub("", text).strip()

        if not text:
            say(
                text="テキストを添えてメンションしてください。\n"
                "例: `@Bot ログイン画面で500エラーが発生する`",
                channel=channel,
                thread_ts=thread_ts,
            )
            return

        try:
            result = say(
                text=":hourglass_flowing_sand: Issue を構造化中...",
                channel=channel,
                thread_ts=thread_ts,
            )
            ts = result["ts"]

            issue = structurize(text)

            blocks = _build_preview_blocks(issue.title, issue.body, issue.labels)

            client.chat_update(
                channel=channel,
                ts=ts,
                text="Issue プレビュー",
                blocks=blocks,
            )

            config = get_config()
            if config.approver_slack_group:
                say(
                    text=f"<!subteam^{config.approver_slack_group}> Issue の提案がありました。レビューしてください",
                    channel=channel,
                    thread_ts=thread_ts,
                )
        except Exception:
            logger.exception("Failed to structurize issue")
            say(
                text=":x: Issue の構造化に失敗しました。しばらくしてから再度お試しください。",
                channel=channel,
                thread_ts=thread_ts,
            )

    @app.action("issue_create")
    def handle_create(ack, body, client):
        ack()

        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        message_ts = body["message"]["ts"]

        if not _is_approver(client, user_id):
            client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text=":no_entry: 承認権限がありません",
            )
            return

        issue_data = _extract_issue_data(body)
        if not issue_data:
            client.chat_update(
                channel=channel,
                ts=message_ts,
                text=":x: Issue データの取得に失敗しました。再度お試しください。",
                blocks=_build_result_blocks(
                    ":x: Issue データの取得に失敗しました。再度お試しください。"
                ),
            )
            return

        doc_id = body["actions"][0].get("value")

        try:
            client.chat_update(
                channel=channel,
                ts=message_ts,
                text="Issue を作成中...",
                blocks=_build_result_blocks(
                    ":hourglass_flowing_sand: Issue を作成中..."
                ),
            )

            issue = StructuredIssue(
                title=issue_data["title"],
                body=issue_data["body"],
                labels=issue_data.get("labels", []),
            )
            issue_url = create_issue(issue)

            client.chat_update(
                channel=channel,
                ts=message_ts,
                text=f"Issue を作成しました! {issue_url}",
                blocks=_build_result_blocks(
                    f":white_check_mark: Issue を作成しました!\n<{issue_url}>"
                ),
            )

            if doc_id:
                _delete_issue_data(doc_id)
        except Exception:
            logger.exception("Failed to create issue")
            client.chat_update(
                channel=channel,
                ts=message_ts,
                text="Issue の作成に失敗しました。",
                blocks=_build_result_blocks(
                    ":x: Issue の作成に失敗しました。しばらくしてから再度お試しください。"
                ),
            )

    @app.action("issue_cancel")
    def handle_cancel(ack, body, client):
        ack()

        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        message_ts = body["message"]["ts"]

        if not _is_approver(client, user_id):
            client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text=":no_entry: 承認権限がありません",
            )
            return

        doc_id = body["actions"][0].get("value")
        if doc_id:
            _delete_issue_data(doc_id)

        client.chat_update(
            channel=channel,
            ts=message_ts,
            text="キャンセルされました",
            blocks=_build_result_blocks(":no_entry_sign: キャンセルされました"),
        )
