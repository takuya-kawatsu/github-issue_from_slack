import logging

import functions_framework
from flask import Request, Response
from slack_bolt import App, BoltRequest, BoltResponse
from slack_bolt.adapter.flask import SlackRequestHandler

from src.config import get_config
from src.handlers import register_handlers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _create_app() -> App:
    config = get_config()
    app = App(
        token=config.slack_bot_token,
        signing_secret=config.slack_signing_secret,
        process_before_response=True,
    )

    @app.middleware
    def skip_retry(req: BoltRequest, resp: BoltResponse, next):
        retry_num = req.headers.get("x-slack-retry-num")
        if retry_num:
            logger.info("Skipping retry #%s (reason: %s)",
                        retry_num,
                        req.headers.get("x-slack-retry-reason"))
            return BoltResponse(status=200, body="ok")
        return next()

    register_handlers(app)
    return app


app = _create_app()
handler = SlackRequestHandler(app)


@functions_framework.http
def slack_events(request: Request) -> Response:
    # Slack URL verification (challenge) - handle before signature check
    if request.is_json:
        body = request.get_json(silent=True)
        if body and body.get("type") == "url_verification":
            return Response(body["challenge"], status=200, content_type="text/plain")
    return handler.handle(request)


if __name__ == "__main__":
    from flask import Flask

    flask_app = Flask(__name__)

    @flask_app.route("/", methods=["POST"])
    def index():
        return handler.handle(flask_app.make_default_options_response())

    @flask_app.route("/slack/events", methods=["POST"])
    def events():
        from flask import request
        return handler.handle(request)

    flask_app.run(port=3000, debug=True)
