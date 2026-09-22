# Install with a coding agent

Instructions for a coding agent (Claude Code, Codex, Cursor, …) asked to install
VoiceStudio. The short prompt in the [README](../../README.md#install-with-prompt)
points here. Complete the setup, not just a plan.

## Rules

- Install the **Electron** desktop app. Tauri is archived; never install or launch it.
- Reuse existing data, settings and models. Ask before downloading a model: state its size and license.
- Keep cloud services and analytics opt-in.
- Verify the actual compute device; do not assume GPU support.
- Name any permission or manual step you cannot perform instead of skipping it.

## Steps

1. **Load context.** Read the install guide for this OS ([macOS](macos.md) ·
   [Windows](windows.md) · [Linux](linux.md); [Docker](docker.md) is a headless backend, not the desktop app),
   [performance](../performance.md) and
   [`skills/voicestudio/SKILL.md`](../../skills/voicestudio/SKILL.md). If your agent
   supports skills, install it with `npx skills add debpalash/VoiceStudio`.
2. **Inspect the device.** OS, CPU architecture, GPU, RAM/VRAM, free disk, and any
   existing VoiceStudio install, backend or downloaded models.
3. **Install.**
   - Default: the latest stable release asset named `VoiceStudio-Electron-*` for this
     OS and architecture, or the [install script](script.md).
   - From source: follow [`electron/README.md`](../../electron/README.md) —
     `bun install`, `bun run setup:api`, `bun run dev` from the repository root. Let
     Electron supervise the backend; do not start a second one.
   - Migrating from Tauri: back up first and follow the
     [migration guide](../electron-migration.md).
4. **Configure.** Pick a supported voice-cloning engine and acceleration that fit this
   device. Keep working defaults and install required dependencies.
5. **Verify.** Start the app and check `/health` at the backend address (default
   `http://localhost:3900`); discover the API through `/openapi.json`. If the chosen
   engine's model is not installed (`GET /models`), state its size and license, get
   consent, then install it with `POST /models/install` and wait on
   `GET /models/install/status`. Generate a short clip with a bundled or authorized
   voice and confirm the audio file plays.
6. **Report.** Installed version, engine, actual device, data location, audio output
   path, and how to reopen the app.
