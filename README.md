# github-issue_from_slack

SlackでBotにメンションすると、Gemini API でテキストを構造化し、GitHub Issueを自動作成するBot。

## アーキテクチャ

```
Slack (メンション)
  → Cloud Functions (HTTP)
    → GCS からコードベースコンテキスト取得（キャッシュ付き）
    → Gemini API (Gemini 2.5 Pro) でテキスト構造化
    → Firestore に Issue データを保存（UUID をキーに）
    → Slack にプレビュー表示（Block Kit、ボタン value は UUID）
    → 承認者が「作成」ボタン押下
      → Firestore から UUID で Issue データ取得
      → GitHub API で Issue 作成
      → Firestore のドキュメント削除
      → Slack に結果を投稿
    → 承認者が「キャンセル」ボタン押下
      → Firestore のドキュメント削除
      → Slack にキャンセルメッセージを投稿
```

## 前提条件

このプロジェクトを始めるには、以下のアカウントとツールが必要です。

### アカウント

- **Google Cloud Platform アカウント**: Vertex AI と Cloud Functions を使用するため、GCP プロジェクトが必要です。請求先アカウントが有効化されている必要があります。
- **Slack ワークスペース**: Bot をインストールする Slack ワークスペースの管理者権限（またはアプリインストールの許可）が必要です。
- **GitHub アカウント**: Issue を作成する対象リポジトリへの書き込み権限が必要です。

### ツール

- **Python 3.12**: ランタイム環境
- **Google Cloud CLI (`gcloud`)**: デプロイに使用。[インストール手順](https://cloud.google.com/sdk/docs/install)
- **Git**: ソースコード管理

## はじめ方

### Step 1: リポジトリのクローン

```bash
git clone https://github.com/your-username/github-issue_from_slack.git
cd github-issue_from_slack
```

### Step 2: Python 環境の準備

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 3: GCP プロジェクトの準備

```bash
# GCP にログイン
gcloud auth login

# プロジェクトを設定
gcloud config set project YOUR_PROJECT_ID

# 必要な API を有効化
gcloud services enable cloudfunctions.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable artifactregistry.googleapis.com
gcloud services enable secretmanager.googleapis.com
gcloud services enable aiplatform.googleapis.com
gcloud services enable firestore.googleapis.com
```

### Step 4: Firestore データベースの作成

```bash
# Firestore データベースを作成（ネイティブモード）
gcloud firestore databases create --location=asia-northeast1

# pending_issues コレクションの expire_at フィールドに TTL ポリシーを設定
gcloud firestore fields ttls update expire_at \
  --collection-group=pending_issues \
  --enable-ttl
```

> **注意**: TTL ポリシーにより、24時間経過した未使用のドキュメントは自動削除されます。

### Step 5: GitHub Personal Access Token の取得

1. GitHub の [Settings > Developer settings > Personal access tokens > Fine-grained tokens](https://github.com/settings/personal-access-tokens/new) にアクセス
2. **Token name**: 任意の名前（例: `slack-issue-bot`）
3. **Repository access**: Issue を作成する対象リポジトリを選択
4. **Permissions** → **Repository permissions** → **Issues**: `Read and write`
5. **Generate token** をクリックし、表示されたトークンを控えておく

### Step 6: Slack App の作成と設定

1. [Slack API](https://api.slack.com/apps) にアクセスし、**Create New App** → **From scratch** を選択
2. App 名とワークスペースを指定して作成

#### Bot Token Scopes の設定

1. 左メニュー **OAuth & Permissions** を開く
2. **Scopes** → **Bot Token Scopes** に以下を追加:
   - `app_mentions:read` — メンションの読み取り
   - `chat:write` — メッセージの送信
   - `usergroups:read` — 承認者グループのメンバー取得（承認者機能使用時）
3. ページ上部の **Install to Workspace** をクリック
4. 表示される **Bot User OAuth Token** (`xoxb-...`) を控えておく

#### Signing Secret の確認

1. 左メニュー **Basic Information** を開く
2. **App Credentials** → **Signing Secret** の値を控えておく

> **注意**: Event Subscriptions と Interactivity の設定は、Cloud Functions のデプロイ後に行います（Step 10〜11）。

### Step 7: Secret Manager にシークレットを登録

```bash
# Slack Bot Token
echo -n "xoxb-your-actual-token" | \
  gcloud secrets create slack-bot-token --data-file=-

# Slack Signing Secret
echo -n "your-actual-signing-secret" | \
  gcloud secrets create slack-signing-secret --data-file=-

# GitHub Token
echo -n "github_pat_your-actual-token" | \
  gcloud secrets create github-token --data-file=-

# Gemini API Key
echo -n "your-actual-gemini-api-key" | \
  gcloud secrets create GEMINI_API_KEY --data-file=-
```

Cloud Functions のサービスアカウントにシークレットへのアクセス権を付与:

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### Step 8: コードベースコンテキストの準備（オプション）

対象システムのコードベースコンテキストを GCS にアップロードすると、Issue 生成時にコードベースの知識を活用できます。

```bash
# コンテキストファイルをアップロード
gsutil cp llm_context.md gs://YOUR_GCS_BUCKET/llm_context.md
```

Cloud Functions のサービスアカウントに GCS への読み取り権限を付与:

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')

gsutil iam ch \
  serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com:objectViewer \
  gs://YOUR_GCS_BUCKET
```

> **注意**: `CONTEXT_GCS_BUCKET` を設定しない場合、コンテキスト無しで従来どおり動作します。

### Step 9: Cloud Functions にデプロイ

```bash
gcloud functions deploy issue-bot \
  --gen2 \
  --runtime python312 \
  --region asia-northeast1 \
  --trigger-http \
  --allow-unauthenticated \
  --entry-point slack_events \
  --source . \
  --memory 512Mi \
  --timeout 300s \
  --set-secrets="SLACK_BOT_TOKEN=slack-bot-token:latest,SLACK_SIGNING_SECRET=slack-signing-secret:latest,GITHUB_TOKEN=github-token:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest" \
  --set-env-vars="GITHUB_REPO=owner/repo,GCP_PROJECT_ID=YOUR_PROJECT_ID,GEMINI_MODEL=gemini-2.5-pro,CONTEXT_GCS_BUCKET=YOUR_GCS_BUCKET,CONTEXT_GCS_PATH=llm_context.md"
```

デプロイ後、関数の URL を確認:

```bash
gcloud functions describe issue-bot \
  --region asia-northeast1 \
  --format='value(serviceConfig.uri)'
```

### Step 10: Slack Event Subscriptions の設定

1. [Slack API](https://api.slack.com/apps) → 対象アプリ → 左メニュー **Event Subscriptions**
2. **Enable Events** を **ON** に切り替え
3. **Request URL** に Step 9 で取得した Cloud Functions の URL を入力
4. `Verified` と表示されることを確認
5. **Subscribe to bot events** → **Add Bot User Event** で `app_mention` を追加
6. **Save Changes** をクリック

### Step 11: Interactivity の有効化

1. [Slack API](https://api.slack.com/apps) → 対象アプリ → 左メニュー **Interactivity & Shortcuts**
2. **Interactivity** を **ON** に切り替え
3. **Request URL** に Step 9 で取得した Cloud Functions の URL を入力（Event Subscriptions と同じ URL）
4. **Save Changes** をクリック

> **注意**: Interactivity はプレビューボタン（作成 / キャンセル）の操作に必要です。

### Step 12: 動作確認

1. Slack の任意のチャンネルに Bot を招待:
   ```
   /invite @YourBotName
   ```

2. Bot にメンションしてテキストを送信:
   ```
   @YourBotName ログイン画面で500エラーが発生する。再現手順: 1. ログインページを開く 2. メールアドレスを入力 3. 送信ボタンを押す
   ```

3. Bot が「Issue を構造化中...」と応答し、数秒後にプレビューが表示されます。
4. プレビューを確認し、「作成」ボタンを押すと Issue が作成されます。「キャンセル」ボタンで取り消せます。
5. `APPROVER_SLACK_GROUP` を設定している場合、グループメンバーのみがボタン操作可能です。

## ローカル開発

ローカルで動作確認する場合は、ngrok を使用して Slack からの Webhook をローカルに転送します。

```bash
# 環境変数の設定
cp .env.example .env
# .env を編集して各値を設定

# 依存パッケージのインストール
pip install -r requirements.txt

# ローカルサーバー起動
source .env && functions-framework --target=slack_events --source=src/main.py --port=3000
```

別ターミナルで ngrok を起動:

```bash
ngrok http 3000
```

表示された `https://xxxx.ngrok-free.app` を Slack の Event Subscriptions の Request URL に設定してください。

## テスト

```bash
pip install pytest
pytest tests/ -v
```

## 環境変数一覧

| 変数名 | 説明 | 必須 |
|--------|------|------|
| `SLACK_BOT_TOKEN` | Slack Bot User OAuth Token (`xoxb-...`) | Yes |
| `SLACK_SIGNING_SECRET` | Slack App の Signing Secret | Yes |
| `GITHUB_TOKEN` | GitHub Personal Access Token | Yes |
| `GITHUB_REPO` | 対象リポジトリ (`owner/repo` 形式) | Yes |
| `GEMINI_API_KEY` | Gemini API Key | Yes |
| `GCP_PROJECT_ID` | Google Cloud プロジェクト ID（GCS コンテキスト読み込み用） | Yes |
| `GEMINI_MODEL` | 使用する Gemini モデル (デフォルト: `gemini-2.5-pro`) | No |
| `CONTEXT_GCS_BUCKET` | コードベースコンテキストが格納された GCS バケット名 | No |
| `CONTEXT_GCS_PATH` | GCS バケット内のコンテキストファイルパス (デフォルト: `llm_context.md`) | No |
| `APPROVER_SLACK_GROUP` | Issue 作成を承認できる Slack ユーザーグループ ID (`S...`)。未設定時は全員が操作可能 | No |

## プロジェクト構成

```
github-issue_from_slack/
├── main.py               # Cloud Functions エントリーポイント（src/main.py への橋渡し）
├── requirements.txt      # Python 依存パッケージ
├── Dockerfile            # ローカル開発用コンテナ
├── .env.example          # 環境変数テンプレート
├── .gcloudignore         # デプロイ時の除外設定
├── src/
│   ├── main.py           # Slack Bolt App + Cloud Functions ハンドラー
│   ├── handlers.py       # Slack イベントハンドラー
│   ├── ai_processor.py   # Gemini API テキスト構造化
│   ├── github_client.py  # GitHub Issue 作成
│   └── config.py         # 環境変数からの設定読み込み
└── tests/
    ├── test_ai_processor.py
    ├── test_github_client.py
    └── test_handlers.py
```
