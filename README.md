# github-issue_from_slack

SlackでBotにメンションすると、Vertex AI (Gemini) でテキストを構造化し、GitHub Issueを自動作成するBot。

## アーキテクチャ

```
Slack (メンション)
  → Cloud Functions (HTTP)
    → Vertex AI (Gemini 2.5 Flash) でテキスト構造化
    → GitHub API で Issue 作成
    → Slack に結果を投稿
```

## セットアップ

### 1. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して各値を設定
```

| 変数名 | 説明 |
|--------|------|
| `SLACK_BOT_TOKEN` | Slack Bot User OAuth Token (`xoxb-...`) |
| `SLACK_SIGNING_SECRET` | Slack App の Signing Secret |
| `GITHUB_TOKEN` | GitHub Personal Access Token |
| `GITHUB_REPO` | 対象リポジトリ (`owner/repo` 形式) |
| `GCP_PROJECT_ID` | Google Cloud プロジェクトID |
| `GCP_LOCATION` | リージョン (デフォルト: `asia-northeast1`) |

### 2. Slack App の設定

1. [Slack API](https://api.slack.com/apps) で新しいアプリを作成
2. Bot Token Scopes: `app_mentions:read`, `chat:write`
3. Event Subscriptions で `app_mention` を購読
4. Request URL: デプロイ後の Cloud Functions エンドポイント

### 3. ローカル開発

```bash
pip install -r requirements.txt
functions-framework --target=slack_events --source=src/main.py --port=3000
```

ngrok でトンネリング:
```bash
ngrok http 3000
```

### 4. デプロイ

```bash
gcloud functions deploy issue-bot \
  --gen2 \
  --runtime python312 \
  --region asia-northeast1 \
  --trigger-http \
  --allow-unauthenticated \
  --entry-point slack_events \
  --source . \
  --memory 256Mi \
  --timeout 60s \
  --set-secrets="SLACK_BOT_TOKEN=slack-bot-token:latest,SLACK_SIGNING_SECRET=slack-signing-secret:latest,GITHUB_TOKEN=github-token:latest" \
  --set-env-vars="GITHUB_REPO=owner/repo,GCP_PROJECT_ID=your-project,GCP_LOCATION=asia-northeast1"
```

## テスト

```bash
pip install pytest
pytest tests/
```

## 使い方

Slackで Bot にメンションしてテキストを送信:

```
@Bot ログイン画面で500エラーが発生する。再現手順: 1. ログインページを開く 2. メールアドレスを入力 3. 送信ボタンを押す
```

Bot が自動的にGitHub Issueを作成し、リンクを返信します。
