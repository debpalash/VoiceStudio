# VoiceStudio compatibility entry

The current cross-agent package is [voicestudio](../../../skills/voicestudio/SKILL.md).
For new installations use `npx skills add debpalash/VoiceStudio --skill voicestudio`.

Use the running backend at the user's configured address (default
`http://localhost:3900`). Check `/health`, discover `/openapi.json` and
`/v1/audio/voices`, then use the installed schema for speech, transcription,
profiles, and jobs. The HTTP MCP endpoint is `/mcp`; discover tools from the
connected server instead of assuming this older package's tool inventory.

Launch the installed Electron app if the backend is unavailable. For source
development follow the checkout's Electron README. Existing helpers in
`scripts/` support legacy source installations; inspect their environment
and dependency assumptions before running them.

Model downloads and remote services require the user's choice. Never silently
install models, promise fixed latency, or treat compatibility voice names as
real provider voices. Validate saved audio and asynchronous job completion
before reporting success. Protected backends require configured credentials;
never disable authentication to make an example work.

Source and current setup documentation:
https://github.com/debpalash/VoiceStudio

This archived entry is not an installable skill. Existing installations should
remove the old `omnivoice` / `oss-maintainer` entries and install `voicestudio` /
`voicestudio-maintainer` from the canonical repository. Legacy helpers remain
for existing users; the Electron supervisor is the preferred launcher.
