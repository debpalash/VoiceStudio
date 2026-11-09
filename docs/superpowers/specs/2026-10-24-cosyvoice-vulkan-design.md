# Native CosyVoice GGUF/Vulkan backend

## Goal

Add CosyVoice 3 inference through the community `Lourdle/cosyvoice.cpp` GGML runtime as a separate opt-in VoiceStudio TTS engine. The verified target is Windows x64 with an AMD Radeon RX 6700 XT using Vulkan. Existing Python CosyVoice (`cosyvoice`) remains untouched and keeps its current behavior.

## Evidence and scope

- A local build pinned to `Lourdle/cosyvoice.cpp` commit `67d45feb2f654bbc66cca25bff78464b3ec08fa1` configured with `GGML_VULKAN=ON`, compiled successfully, and exposed `Vulkan0 - AMD Radeon RX 6700 XT`.
- The Q4_K_M CosyVoice3-2512 model generated 24 kHz mono speech from the user's local Mira profile. The server API also generated a sample with an instruct/breathy cue.
- Upstream is an unofficial community port; the result establishes this machine's working route, not general AMD support or full ElevenLabs parity.

## Design

1. Register an independent `cosyvoice-gguf` engine. It advertises CosyVoice 3 languages, reference cloning, free-form instruct text, and 24 kHz output. It does not advertise text-only voice design.
2. Resolve the native runtime from an explicit environment override or the app-managed engine directory. V1 does not download/redistribute executable runtime files: the user builds the pinned source or points at an existing build, and the setup guide lists required dependencies and preserves upstream notices. Resolve the GGUF and two ONNX frontend files only from an explicit, immutable Model Catalogue revision or explicit file overrides. Generation never downloads files implicitly.
3. Probe the exact native runtime's backend list. V1 selects devices through `OMNIVOICE_COSYVOICE_GGUF_BACKEND` (`auto`, `CPU`, or a detected backend name such as `Vulkan0`); the engine metadata and guide show the actual detected target and fallback reason. If automatic mode is selected, choose an available Vulkan device and otherwise use CPU with a visible reason; never claim GPU acceleration from the OS Vulkan driver alone. A dedicated Settings picker is deferred until the backend behavior is established.
4. Cache each reference prompt by a SHA-256 over audio content and transcript, create it with the native CLI frontend once, and reuse it. Keep a loopback-only server resident. Serialize generation and profile changes under one lock; restart it only when the active reference prompt changes. Verify the managed process owns the loopback endpoint before sending audio requests.
5. Convert server WAV responses to mono float tensors at the engine's declared 24 kHz rate. Pass `instruct`, `language`, and seed when supported; report unsupported controls rather than silently advertising them.
6. Document the runtime build/install procedure, the pinned source revision, model assets and licenses, explicit Vulkan selection, validation steps, and known limits. Keep all code modules below 200 lines.

## Safety and compatibility

- No default-engine change, automatic runtime/model download, remote inference, or new access to the local network. The native service binds to loopback and is owned/terminated by VoiceStudio. The manual runtime build is version-pinned and must retain upstream runtime, GGML, ONNX Runtime, and model/component notices; VoiceStudio does not redistribute those binaries in this phase.
- No edits to the current Python CosyVoice implementation or AMD ROCm path.
- The engine is unavailable until both runtime and model files exist; diagnostics explain the missing prerequisite.
- Model and runtime provenance are pinned. The model package identifies Apache-2.0, while commercial use and full component closure remain unreviewed; documentation must not claim broader clearance. The community runtime is MIT-licensed and unofficial, with third-party dependency notices retained in the built runtime.
- The focused implementation issue is linked to umbrella #2468 without closing #2468.

## Acceptance checks

- Unit tests cover path precedence, runtime/device selection, prompt cache keys, request construction, response decoding, and failure cleanup without network access.
- Existing TTS registry and CosyVoice sidecar tests still pass.
- A local end-to-end check against the user's built runtime returns non-silent 24 kHz mono output and logs `Vulkan0 (AMD Radeon RX 6700 XT)`.
- GPU routing is only reported as accelerated after the native runtime reports the selected Vulkan device; concurrent requests/profile switches cannot use a stale or mismatched prompt mapping.
