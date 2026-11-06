# Design references and release contracts

## Current release contracts

The [Desktop Pro checkout contract](desktop-pro-page.md) defines the release
gates. New paid capabilities and checkout changes stay unavailable until those
gates pass. [Model licensing review](../licensing/model-review-workflow.md)
covers model rights separately.

## Design proposals

These documents preserve design rationale and proposals. Their file paths,
progress notes and implementation instructions describe the original snapshot;
check current code and the linked topic guide before using them. A design
proposal does not establish that a feature ships or that an open item is done.
Repository policy comes from [AGENTS.md](../../AGENTS.md) and
[CLAUDE.md](../../CLAUDE.md), including localization and select-control rules.

| Subject | Design | Current reference |
| --- | --- | --- |
| Expressive TTS | [Expression and pronunciation](01-expressive-tts.md) | [Expressive speech](../expressive-speech.md) |
| Conversational agents | [Conversation design](02-conversational-agent.md) | [Agentic voice](../agentic-voice.md) |
| Longform editing | [Editor design](03-longform-studio-editor.md) · [Retained longform designs](longform/README.md) | [Stories and audiobooks](../electron-longform.md) |
| Profiles and workspaces | [Profile unification](voice-studio-unification.md) · [Workspace navigation](workspace-connectivity.md) · [Voice console](voice-console-10x.md) | [Projects](../electron-projects.md) |
| Dictation | [Flow design](2026-07-16-dictation-flow-program.md) | [Dictation guide](../features/dictation.md) |
| Remote authentication | [Security design](2026-08-13-remote-admin-session-hardening.md) | [API authentication](../api-auth.md) |
| License-client proposal | [Candidate client protocol](commercial-license-client.md) | [Checkout release contract](desktop-pro-page.md) |
| Installer investigation | [Review](installer-redesign/review.md) · [Findings](installer-redesign/readme.md) | [Installation troubleshooting](../install/troubleshooting.md) |

For current priorities use the [live roadmap](../ROADMAP.md). Superseded Tauri
implementation plans and duplicate build checklists have been removed; their
history is available through `git log --all -- <path>`.
