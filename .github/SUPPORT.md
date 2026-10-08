# Support

## Where to get help

| Channel | Best for |
|---|---|
| [Discord](https://discord.gg/bzQavDfVV9) — `#help` | Setup problems, quick questions, sharing results |
| [GitHub Issues](https://github.com/debpalash/VoiceStudio/issues) | Bugs and feature requests — use the templates; attach the diagnostic bundle (Settings → About → "Save diagnostic bundle") |
| [GitHub Discussions](https://github.com/debpalash/VoiceStudio/discussions) | Design questions, ideas, show & tell |
| Security issues | **Never a public issue** — see [SECURITY.md](SECURITY.md) for private reporting |

## Uninstalling / removing all data

VoiceStudio is fully local — there's nothing to deactivate, just folders to
delete. `scripts/uninstall.sh` (macOS/Linux) or `scripts\uninstall.ps1`
(Windows) lists every VoiceStudio folder with its size (dry-run first) and
removes them on `--yes`. The complete per-platform path list is in
[docs/install/uninstall.md](../docs/install/uninstall.md).

## Model sources we support

VoiceStudio is built on the idea that everything it runs is **open and available
to everyone**: free, public models with verifiable sources and licenses
(Hugging Face repos, official project releases), so the whole community can
use, test, and debug the same thing.

**We do not support privately sold, paywalled, or gated model files.** A model
delivered privately can't be verified, reproduced, or shared — it doesn't fit
the project's goals, and issues involving such models will be politely closed.
As a general safety rule, never run executables bundled inside any model
archive.

## Before filing a bug

1. Update to the latest release (or `main` if you follow previews) — fixes ship continuously.
2. Run the in-app self-check: **Settings → About → Run self-check**.
3. Search existing issues; add a 👍 + your details to an existing one rather than opening a duplicate.

## Response expectations

This is an open-source project maintained with the help of an automated triage
bot: issues are typically triaged within hours and every report gets a human-
approved response. Reproducible reports with a diagnostic bundle get fixed
fastest.
