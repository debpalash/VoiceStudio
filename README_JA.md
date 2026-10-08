<div align="center">
  <img src="docs/logo.png" alt="VoiceStudio" width="88" />
  <h1>VoiceStudio</h1>
  <p>
    <a href="https://trendshift.io/repositories/28176?utm_source=repository-badge&amp;utm_medium=badge&amp;utm_campaign=badge-repository-28176" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/repositories/28176" alt="Trendshift における VoiceStudio のランキング" width="220" height="48" /></a>
  </p>
  <p><strong>646 言語に対応した、オープンソースの音声クローン・ボイスデザイン・動画吹き替え・音声入力・文字起こし・オーディオブック制作ツール。</strong></p>
  <p>
    <a href="https://voicestudio.sh/?utm_source=github&utm_medium=readme&utm_campaign=project">ウェブサイト</a> ·
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest">ダウンロード</a> ·
    <a href="#はじめに">はじめに</a> ·
    <a href="#ドキュメント">ドキュメント</a> ·
    <a href="https://discord.gg/bzQavDfVV9">Discord</a> ·
    <a href="README.md">English</a> ·
    <a href="README_CN.md">简体中文</a>
  </p>
  <p>
    <a href="https://github.com/debpalash/VoiceStudio/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/debpalash/VoiceStudio/ci.yml?branch=main" alt="CI" /></a>
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest"><img src="https://img.shields.io/github/v/release/debpalash/VoiceStudio" alt="最新リリース" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0" /></a>
  </p>
</div>

![Electron アプリのツアー：音声クローン、ボイスデザイン、吹き替え、モデル管理](docs/media/electron/voicestudio.gif)

## あなたの声で、あなたのワークフローを。

| つくる | 仕上げる | つなぐ |
| :--- | :--- | :--- |
| 声をクローン、または独自にデザイン | タイミングを合わせた音声で動画を吹き替え | エージェント向けのローカル API と MCP |
| フローティングウィジェットで音声入力 | ストーリー、オーディオブック、バッチ処理 | オプションのリモートワーカー |

まずは **VoiceStudio**（デフォルト、k2-fsa/OmniVoice を使用）から始めるか、別のエンジンを選択してください。[機能とエンジンの一覧](docs/feature-catalog.md)。

ローカルのワークフローはお使いのハードウェア上で動作します。リモートサービスは任意で、利用状況の分析には同意が必要です。

<details>
<summary><strong>ワークスペースを見る</strong> · クローン、吹き替え、デザイン、モデル</summary>

<table>
  <tr>
    <td><img src="docs/media/electron/voice-cloning.png" alt="同梱のデモ音声を使った Electron の音声クローンワークスペース" width="100%" /></td>
    <td><img src="docs/media/electron/dubbing.png" alt="Electron の動画吹き替えワークスペース" width="100%" /></td>
  </tr>
  <tr><td align="center">音声クローン</td><td align="center">動画吹き替え</td></tr>
  <tr>
    <td><img src="docs/media/electron/voice-design.png" alt="Electron のボイスデザインワークスペースで声を説明する" width="100%" /></td>
    <td><img src="docs/media/electron/models.png" alt="ローカル音声モデルのインストールと管理" width="100%" /></td>
  </tr>
  <tr><td align="center">ボイスデザイン</td><td align="center">ローカルモデル</td></tr>
</table>

<img width="2628" height="1950" alt="VoiceStudio デスクトップワークスペース" src="https://github.com/user-attachments/assets/b474497d-a453-49a3-a2dd-f023ec6b7659" />

</details>

## はじめに

### ワンコマンドインストール（macOS / Linux）

```sh
# 最新の Electron リリース
curl -fsSL https://voicestudio.sh/install | sh

# 公開済みの特定の Electron リリース（X.Y.Z を置き換えてください）
curl -fsSL https://voicestudio.sh/install | sh -s -- --version X.Y.Z

# 現在の main をビルドしてデスクトップアプリをインストール
curl -fsSL https://voicestudio.sh/install | sh -s -- --main

# データを残したままアプリをアンインストール
curl -fsSL https://voicestudio.sh/install | sh -s -- --uninstall
```

リリース版のダウンロードには curl と SHA-256 ツールが必要です。`--main` には Git、
Node.js 22 以上、Bun、Rust/Cargo、および各プラットフォームのビルドツールが必要です。詳しくは
[インストーラーの前提条件と動作](docs/install/script.md)を参照してください。
インストーラーは設定、プロジェクト、モデルを保持します。旧バージョンを指定する場合は
Electron パッケージを含んでいる必要があり、アーカイブ済みの Tauri ビルドにフォールバックすることはありません。

[Releases](https://github.com/debpalash/VoiceStudio/releases/latest) からダウンロードし、お使いのプラットフォームのガイドに従ってください：

**[macOS](docs/install/macos.md) · [Windows](docs/install/windows.md) · [Linux](docs/install/linux.md) · [Docker](docs/install/docker.md)**

**音声クローン**を開き、声を選ぶかクリアな参照音声を追加し、テキストを入力して生成します。求められたら必要なモデルをインストールしてください。必要なハードウェアはエンジンによって異なります。[パフォーマンス](docs/performance.md)を参照してください。

### プロンプトでインストール

コーディングエージェント（Claude Code、Codex、Cursor など）に貼り付けてください：

```text
Install the VoiceStudio Electron app on this device and verify it works, following
https://github.com/debpalash/VoiceStudio/blob/main/docs/install/agent.md
```

[エージェントガイド](docs/install/agent.md)では、ハードウェアの検出、既存データの再利用、
モデルダウンロード前の確認、テスト生成について説明しています。スキルに対応したエージェントでは
`npx skills add debpalash/VoiceStudio` も実行できます。

<details>
<summary><strong>ソースから Electron プレビューを実行する</strong></summary>

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run setup:api  # Electron を起動する前に Python の依存関係を準備
bun run dev
```

前提条件とバックエンドの設定については [Electron のセットアップ](electron/README.md)を参照してください。

`bun run smoke-test` を使うと、隔離されたパッケージ版 Electron アプリをビルドして起動できます。
ネットワーク経由のマネージドランタイムのインストールを確認するには `-- --install` を追加してください。

</details>

> **Electron が唯一のデスクトップアプリおよび Web UI です。** バージョン 0.5.3 が最後の Tauri リリースでした。既存の Tauri ユーザーは [Electron を別途インストール](docs/electron-migration.md)する必要があります。廃止された Tauri シェルと旧 UI のエントリーポイントは削除されています。

## ドキュメント

| 目的 | 参照先 |
|---|---|
| セットアップのヘルプ | [トラブルシューティング](docs/install/troubleshooting.md) · [モデルのダウンロード](docs/downloading-models.md) |
| モデルと音質 | [エンジンガイド](docs/engines/README.md) · [ベンチマーク](docs/benchmarks.md) |
| 連携 | [ローカル API](docs/speech-platform.md) · [MCP](docs/mcp.md) · [サンプル](examples/README.md) |
| 開発 | [コントリビューション](.github/CONTRIBUTING.md) · [Electron](electron/README.md) · [変更履歴](CHANGELOG.md) |

エージェントスキル：`npx skills add debpalash/VoiceStudio` — 音声ワークフローには **voicestudio**、リポジトリのメンテナンスには **voicestudio-maintainer** を選択してください。

## スポンサー

<a href="https://forms.gle/2PYCvd39hbwijzX37"><img src="docs/media/sponsor-slot.svg" alt="あなたのブランドを — VoiceStudio の注目スポンサー枠に応募" width="640" /></a>

**注目パートナーになりませんか。** [有料掲載に応募](https://forms.gle/2PYCvd39hbwijzX37) · [メールで問い合わせ](mailto:partner@voicestudio.sh)

開発を支援する：[Ko-fi](https://ko-fi.com/debpalash) · [PayPal](https://paypal.me/palashCoder) · [スポンサーシップの詳細](SPONSORS.md)

## ライセンスと責任ある利用

[AGPL-3.0](LICENSE)。モデルにはそれぞれ独自のライセンスがあります。商用利用の前に確認してください。声のクローンは必ず許可を得たうえで行ってください。詳しくは[ライセンスの詳細](LICENSE-NOTICE.md)を参照してください。
