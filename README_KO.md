<div align="center">
  <img src="docs/logo.png" alt="VoiceStudio" width="88" />
  <h1>VoiceStudio</h1>
  <p>
    <a href="https://trendshift.io/repositories/28176?utm_source=repository-badge&amp;utm_medium=badge&amp;utm_campaign=badge-repository-28176" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/repositories/28176" alt="Trendshift의 VoiceStudio 순위" width="220" height="48" /></a>
  </p>
  <p><strong>646개 언어를 지원하는 오픈소스 음성 복제, 보이스 디자인, 영상 더빙, 받아쓰기, 음성 전사, 오디오북 제작 도구.</strong></p>
  <p>
    <a href="https://voicestudio.sh/?utm_source=github&utm_medium=readme&utm_campaign=project">웹사이트</a> ·
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest">다운로드</a> ·
    <a href="#시작하기">시작하기</a> ·
    <a href="#문서">문서</a> ·
    <a href="https://discord.gg/bzQavDfVV9">Discord</a> ·
    <a href="README.md">English</a> ·
    <a href="README_CN.md">简体中文</a> ·
    <a href="README_JA.md">日本語</a>
  </p>
  <p>
    <a href="https://github.com/debpalash/VoiceStudio/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/debpalash/VoiceStudio/ci.yml?branch=main" alt="CI" /></a>
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest"><img src="https://img.shields.io/github/v/release/debpalash/VoiceStudio" alt="최신 릴리스" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0" /></a>
    <a href="https://supermetry.com"><img src="https://app.supermetry.com/px/zfjiv7psp4ed.svg?c=VoiceStudio&style=stats" alt="조회수" /></a>
  </p>
</div>

![Electron 앱 둘러보기: 음성 복제, 보이스 디자인, 더빙, 모델 관리](docs/media/electron/voicestudio.gif)

## 나의 목소리로, 나의 워크플로를.

| 만들기 | 제작하기 | 연결하기 |
| :--- | :--- | :--- |
| 목소리를 복제하거나 직접 디자인 | 타이밍을 맞춘 음성으로 영상 더빙 | 에이전트를 위한 로컬 API와 MCP |
| 플로팅 위젯으로 받아쓰기 | 스토리, 오디오북, 일괄 작업 | 재사용 가능한 레시피와 감시 폴더 |

기본 엔진인 **VoiceStudio**(k2-fsa/OmniVoice 기반)로 시작하거나 다른 엔진을 선택하세요. [기능 및 엔진 카탈로그](docs/feature-catalog.md).

로컬 워크플로는 사용자의 하드웨어에서 실행됩니다. 원격 서비스는 선택 사항이며, 사용 통계 수집은 사용자 동의가 있어야만 이루어집니다.

<details>
<summary><strong>워크스페이스 살펴보기</strong> · 복제, 더빙, 디자인, 모델</summary>

<table>
  <tr>
    <td><img src="docs/media/electron/voice-cloning.png" alt="기본 제공 데모 음성이 열린 Electron 음성 복제 워크스페이스" width="100%" /></td>
    <td><img src="docs/media/electron/dubbing.png" alt="Electron 영상 더빙 워크스페이스" width="100%" /></td>
  </tr>
  <tr><td align="center">음성 복제</td><td align="center">영상 더빙</td></tr>
  <tr>
    <td><img src="docs/media/electron/voice-design.png" alt="Electron 보이스 디자인 워크스페이스에서 목소리를 묘사하기" width="100%" /></td>
    <td><img src="docs/media/electron/models.png" alt="로컬 음성 모델 설치 및 관리" width="100%" /></td>
  </tr>
  <tr><td align="center">보이스 디자인</td><td align="center">로컬 모델</td></tr>
</table>

<img width="2628" height="1950" alt="VoiceStudio 데스크톱 워크스페이스" src="https://github.com/user-attachments/assets/b474497d-a453-49a3-a2dd-f023ec6b7659" />

</details>

## 시작하기

### 명령 한 줄로 설치 (macOS / Linux)

```sh
# 최신 Electron 릴리스
curl -fsSL https://voicestudio.sh/install | sh

# 특정 Electron 릴리스 (X.Y.Z를 원하는 버전으로 변경)
curl -fsSL https://voicestudio.sh/install | sh -s -- --version X.Y.Z

# 현재 main 브랜치를 빌드해 데스크톱 앱 설치
curl -fsSL https://voicestudio.sh/install | sh -s -- --main

# 데이터는 유지하고 앱만 제거
curl -fsSL https://voicestudio.sh/install | sh -s -- --uninstall
```

Windows에서는 PowerShell에서 `irm https://voicestudio.sh/install | iex`를 실행하세요.

`voicestudio.sh/install`에 접속할 수 없거나 404가 반환되면, 같은 스크립트를
저장소에서 직접 실행하거나 저장소를 클론한 뒤 `scripts/install.sh`를 실행하세요.

```bash
curl -fsSL https://raw.githubusercontent.com/debpalash/VoiceStudio/main/scripts/install.sh | sh
```

릴리스를 내려받으려면 curl과 SHA-256 도구가 필요합니다. `--main`을 쓰려면 Git,
Node.js 22+, Bun, Rust/Cargo와 플랫폼별 빌드 도구가 필요합니다.
[설치 스크립트의 요구 사항과 동작](docs/install/script.md)을 참고하세요.
설치 스크립트는 설정, 프로젝트, 모델을 그대로 보존합니다. 이전 버전을 설치하려면
해당 버전에 Electron 패키지가 있어야 하며, 보관된 Tauri 빌드로 대체 설치하지 않습니다.

또는 [Releases](https://github.com/debpalash/VoiceStudio/releases/latest)에서 내려받은 뒤(macOS `.dmg`, Windows `.exe`, Linux `.AppImage` 또는 `.deb`) 플랫폼별 가이드를 따르세요.

**[macOS](docs/install/macos.md) · [Windows](docs/install/windows.md) · [Linux](docs/install/linux.md) · [Docker](docs/install/docker.md)**

| 하드웨어 | 지원 |
|---|---|
| NVIDIA GPU (Windows / Linux) | CUDA 가속 |
| Apple Silicon | Metal (MPS) 가속 |
| 전용 GPU 없음 (Intel/AMD 내장 그래픽, 구형 PC) | CPU로 모든 기능 사용 가능(속도는 느림). 설치 시 용량이 작은 CPU용 PyTorch 빌드를 설치합니다(여유 디스크 약 5 GB 필요) |
| Windows on ARM (Snapdragon X 등) | **실험적 지원**, 검증 진행 중: 네이티브 ARM64 앱, 에뮬레이션으로 동작하는 x64 Python 백엔드, CPU 전용 |
| Intel Mac | 앱 UI만 지원, 원격 백엔드에 연결해서 사용 ([이유](docs/install/macos.md)) |

**음성 복제**를 열고 목소리를 고르거나 깨끗한 참조 녹음을 추가한 다음, 텍스트를 입력하고 생성하세요. 안내가 나오면 필요한 모델을 설치하세요. 필요한 하드웨어 사양은 엔진마다 다릅니다. [성능](docs/performance.md) 문서를 참고하세요.

### 프롬프트로 설치

코딩 에이전트(Claude Code, Codex, Cursor 등)에 붙여 넣으세요.

```text
Install the VoiceStudio Electron app on this device and verify it works, following
https://github.com/debpalash/VoiceStudio/blob/main/docs/install/agent.md
```

[에이전트 가이드](docs/install/agent.md)는 하드웨어 감지, 기존 데이터 재사용, 모델 다운로드 전 사용자 확인,
테스트 생성까지 다룹니다. 스킬을 지원하는 에이전트라면 `npx skills add debpalash/VoiceStudio`를
실행할 수도 있습니다.

<details>
<summary><strong>소스에서 Electron 프리뷰 실행하기</strong></summary>

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run setup:api  # Electron을 시작하기 전에 Python 의존성 준비
bun run dev
```

사전 요구 사항과 백엔드 설정은 [Electron 설정](electron/README.md)을 참고하세요.

`bun run smoke-test`를 실행하면 격리된 패키지 Electron 앱을 빌드하고 실행합니다.
네트워크를 사용하는 관리형 런타임 설치 점검을 하려면 `-- --install`을 덧붙이세요.

</details>

> **데스크톱 앱과 웹 UI는 Electron 하나뿐입니다.** 0.5.3이 마지막 Tauri 릴리스였습니다. 기존 Tauri 사용자는 [Electron을 별도로 설치](docs/electron-migration.md)해야 합니다. 지원이 종료된 Tauri 셸과 레거시 UI 진입점은 제거되었습니다.

## 문서

| 필요한 것 | 여기서 시작 |
|---|---|
| 설치 도움말 | [문제 해결](docs/install/troubleshooting.md) · [모델 다운로드](docs/downloading-models.md) |
| 모델과 음질 | [엔진 가이드](docs/engines/README.md) · [벤치마크](docs/benchmarks.md) |
| 연동 | [로컬 API](docs/speech-platform.md) · [MCP](docs/mcp.md) · [예제](examples/README.md) |
| 휴대폰 컴패니언 (프리뷰) | [iOS 및 Android 앱](docs/mobile.md) |
| 개발 | [기여하기](.github/CONTRIBUTING.md) (첫 PR을 위한 [CLA 서명 안내](.github/CONTRIBUTING.md#contribution-licensing)) · [Electron](electron/README.md) · [변경 기록](CHANGELOG.md) |

에이전트 스킬: `npx skills add debpalash/VoiceStudio` — 오디오 워크플로에는 **voicestudio**를, 저장소 유지 관리에는 **voicestudio-maintainer**를 선택하세요.

## 후원사

<a href="https://forms.gle/2PYCvd39hbwijzX37"><img src="docs/media/sponsor-slot.svg" alt="여러분의 브랜드 — VoiceStudio 공식 후원사 자리에 지원하세요" width="640" /></a>

**공식 파트너가 되어 주세요.** [유료 게재 신청](https://forms.gle/2PYCvd39hbwijzX37) · [이메일 문의](mailto:partner@voicestudio.sh)

개발 후원: [GitHub Sponsors](https://github.com/sponsors/debpalash) · [Ko-fi](https://ko-fi.com/debpalash) · [PayPal](https://paypal.me/palashCoder) · [후원 안내](SPONSORS.md)

## 라이선스와 책임 있는 사용

[AGPL-3.0](LICENSE)은 애플리케이션에 적용되며, 그 조건에 따른 상업적 사용도 포함합니다. 모델 가중치, 토크나이저, 음성, 생성 결과물에 대한 조건은 별개이며, 유료 앱 라이선스가 이러한 권리를 부여하지는 않습니다. 목소리는 반드시 허락을 받은 경우에만 복제하세요. 모델 관리자는 이제 [공유 인벤토리](docs/licensing/model-inventory.md)를 바탕으로 버전별 모델 출처 고지, 근거 링크, 미해결 검토 항목을 보여 줍니다. 선택 기능인 [검토 완료 파일 미리보기](docs/licensing/model-review-workflow.md)는 일반 다운로드를 제한하지 않으며 상업적 사용 허가를 보증하지도 않습니다. 자세한 내용은 [라이선스 상세](LICENSE-NOTICE.md)를 참고하세요.
