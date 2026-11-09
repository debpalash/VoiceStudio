# Implementation plan: CosyVoice GGUF/Vulkan

## Task 1 — Pin engine inputs

- [x] Pin the community source revision, model repository revision, expected model files, frontend ONNX paths, and model license record.
- [x] Add tests for runtime/model path resolution and downloaded-model cache discovery before implementation.

## Task 2 — Add runtime discovery and routing

- [x] Resolve the native server/CLI from an explicit override or the app-managed engine folder.
- [x] Probe `cosyvoice-cli --list-backends`, parse its actual device names, implement explicit device selection and automatic Vulkan-then-CPU fallback.
- [x] Test authoritative binary overrides, explicit missing backend, automatic selection, and CPU-only runtime.

## Task 3 — Implement reusable voice prompts

- [x] Hash reference audio bytes plus transcript, generate a prompt-speech GGUF once with the pinned runtime frontend assets, and atomically cache it under app data.
- [x] Test stable cache keys, transcript invalidation, and cleanup of failed temporary outputs.

## Task 4 — Add the managed local server adapter

- [x] Add a process-owned, loopback-only server lifecycle with a random port, API key, random served model id, health/model identity checks, request timeout, WAV decoding, and idempotent shutdown.
- [x] Cache/reuse the prompt and restart only when its content key changes.
- [x] Test request payloads, returned waveform shape/rate, endpoint construction, and process cleanup; exercise real startup and generation in the hardware smoke test.

## Task 5 — Register engine and user-visible setup

- [x] Register `cosyvoice-gguf` as an independent lazy engine with honest capabilities and routing metadata.
- [x] Add the model catalog/license metadata, manual native-runtime build/install instructions, limitations, and an Unreleased changelog entry.
- [x] Preserve the existing `cosyvoice` and default-engine behavior.

## Task 6 — Validate

- [x] Run focused backend tests, catalog/license validation, and documentation checks.
- [x] Run the live RX 6700 XT integration smoke with `Vulkan0`, Mira reference prompt, one ordinary line, and one breathy/instruct line. Verify mono 24 kHz nonzero outputs and runtime device identity.
- [x] Review the diff for scope, clean install behavior, no secret/local paths, and all new Python modules below 200 lines.
- [ ] Open a focused PR linked to #3247 and request CI/cross-agent review; keep umbrella issue #2468 open.

## Validation notes

- 325 focused tests: 321 passed, 2 skipped, and 2 unrelated IndexTTS ROCm installer expectations failed (`test_rocm_host_keeps_a_rocm_torch_install_healthy` and `test_non_rocm_hosts_keep_installed_means_installed`). The same failures reproduced before implementation.
- Separate model/license/revision checks: 17 passed; license inventory validator passed with 59 model records.
- The actual local `cosyvoice.cpp` server selected `Vulkan0 - AMD Radeon RX 6700 XT` and generated ordinary plus breathy 24 kHz mono WAVs through the adapter.
