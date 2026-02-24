import logging
import re

from slack_bolt import App

from src.ai_processor import structurize
from src.github_client import create_issue

logger = logging.getLogger(__name__)

MENTION_PATTERN = re.compile(r"<@[A-Z0-9]+>")


def register_handlers(app: App) -> None:
    @app.event("app_mention")
    def handle_app_mention(event, say):
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
            say(
                text=":hourglass_flowing_sand: Issue を作成中...",
                channel=channel,
                thread_ts=thread_ts,
            )

            issue = structurize(text)
            issue_url = create_issue(issue)

            say(
                text=f":white_check_mark: Issue を作成しました!\n<{issue_url}>",
                channel=channel,
                thread_ts=thread_ts,
            )
        except Exception:
            logger.exception("Failed to create issue")
            say(
                text=":x: Issue の作成に失敗しました。しばらくしてから再度お試しください。",
                channel=channel,
                thread_ts=thread_ts,
            )
