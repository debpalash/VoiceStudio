# Reliable native Vulkan detection implementation plan

> For agentic workers: use the executing-plans workflow, completing the checks below before publishing.

Goal: preserve native GPU acceleration when a Windows hidden Vulkan report hangs, and prevent desktop releases from silently omitting the Vulkan runtime.

Architecture: retain the full capability report first; on a failed or timed-out full report, use the bounded headless summary. Match summary adapters to the OS inventory by unique vendor and exact normalized name for 64-bit VRAM; preserve unknown BF16 as unknown. Split adapter types, text parsing and process invocation into modules under 200 lines, preserving vulkan_auto public exports.

Tech stack: Python, pytest, Vulkan Tools, GitHub Actions, Electron.

Spec: user request in this chat: make a workable AMD/Intel GPU build and submit the changes upstream for the next release.

## Constraints
- No model/engine switch or licence acceptance without user choice.
- Keep CPU fallback, device numbering, duplicate-driver filtering and explicit overrides.
- No version bump or upstream release. Local preview must retain rollback and existing data.
- Every touched Python module remains below 200 lines.

## Review focus
- Summary success must prove a GPU driver, not merely the existence of the Vulkan loader.
- Match only unique inventory adapters; never borrow memory from another GPU.
- Failed native commands must not promote their partial output to verified hardware.
- Bound both probes and keep them hidden on Windows.
- Windows desktop build fails if the Vulkan module is missing.

## Tasks
- [x] Add regressions for full-report timeout, summary fallback, failed return codes, identity-safe VRAM matching, and missing Windows Vulkan payload; observe failure.
- [x] Implement bounded fallback and modularize existing discovery without changing its public surface.
- [x] Require Windows Vulkan SDK/build output and update user documentation.
- [x] Run focused GPU tests, build/type checks and native hardware probes.
- [x] Obtain independent review, address findings, and open upstream PR #3648 linked to issue #3645. Assignment/labels/milestone were attempted but GitHub rejected them for missing upstream permissions.
- [x] Install the verified local preview with rollback and check PIN-protected network access. After owner licence approval, the installed preview generated a valid 3.53-second WAV; native logs confirmed LM and codec on Vulkan0 / RX 6700 XT.
