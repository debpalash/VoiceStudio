# Project handoff cross-platform fixtures

These deterministic ZIP_STORED `.vsproject` files use format
`voicestudio.project`, version 1. The same bytes are imported by Python,
Swift and Kotlin tests. `desktop-book.json` is the readable manifest.

The book includes current Electron `LongformProject.draft` fields, book
identity, pronunciation, render overrides and an unknown future setting.
The dub includes mono 24 kHz source audio/PCM, a stable segment ID,
translations, gain, speaker, timing and a separate reference clip.
Audio is a generated constant sample used only as test input.
