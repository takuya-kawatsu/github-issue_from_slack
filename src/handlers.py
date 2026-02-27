import logging
import re

from slack_bolt import App
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.ai_processor import StructuredIssue, structurize
from src.config import get_config
from src.github_client import create_issue

logger = logging.getLogger(__name__)

MENTION_PATTERN = re.compile(r"<@[A-Z0-9]+>")

PREVIEW_BODY_LIMIT = 3000


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _build_preview_blocks(title: str, body: str, labels: list[str]) -> list[dict]:
    label_text = ", ".join(f"`{label}`" for label in labels) if labels else "なし"
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
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "キャンセル"},
                    "style": "danger",
                    "action_id": "issue_cancel",
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


def _extract_issue_from_metadata(body: dict) -> dict | None:
    message = body.get("message", {})
    metadata = message.get("metadata")

    if metadata and metadata.get("event_payload"):
        return metadata["event_payload"]

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

            metadata = {
                "event_type": "issue_preview",
                "event_payload": {
                    "title": issue.title,
                    "body": issue.body,
                    "labels": issue.labels,
                },
            }

            blocks = _build_preview_blocks(issue.title, issue.body, issue.labels)

            client.chat_update(
                channel=channel,
                ts=ts,
                text="Issue プレビュー",
                blocks=blocks,
                metadata=metadata,
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

        issue_data = _extract_issue_from_metadata(body)
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

        client.chat_update(
            channel=channel,
            ts=message_ts,
            text="キャンセルされました",
            blocks=_build_result_blocks(":no_entry_sign: キャンセルされました"),
        )
