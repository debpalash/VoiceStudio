# Intel Mac Radeon: experimental native runtime findings

Intel Mac support for the application backend remains limited as documented in
[macOS installation](../install/macos.md). This note records a source-level
Radeon workaround; it does not turn the packaged Intel build into a supported
GPU build, and it does not claim that Intel integrated graphics work.

## What was reproduced

A discrete AMD Radeon Pro 5500M (8 GB) on macOS 15.6.1 ran the OmniVoice
model and audio tokenizer with the native `omnivoice.cpp` Metal runtime.
The base and tokenizer were Q8_0, pinned to the existing GGUF model revision.
Speech-model inference and tokenizer graph operations ran on Metal. CPU work
still included text preparation, audio resampling and process coordination.

Two independent issues required source changes:

1. Discrete-Metal tensor upload/readback attempted to wrap ordinary caller
   pointers with `newBufferWithBytesNoCopy`, which requires aligned memory.
   Owned shared staging buffers removed the alignment assertion.
2. Concurrent Metal codec execution produced corrupted output. A separate
   codec backend with serialized command execution fixed the observed
   corruption while preserving concurrency in the speech-model backend.
   This is an opt-in workaround, not a general scheduling root-cause fix.

A model-free regression test exercises unaligned upload/readback and tensor
offsets. CPU versus serialized Metal decoder output was compared locally;
full synthesis was also checked for finite, unclipped output and matching
English/Urdu speech recognition. Other Intel Mac GPUs have not been verified.
No personal recordings or generated voice samples are included.

## Where the work belongs

The source fixes and explicit experimental build recipe belong in
[omnivoice.cpp](https://github.com/ServeurpersoCom/omnivoice.cpp), including
review of its pinned GGML dependency. The experimental recipe is proposed in
[omnivoice.cpp #24](https://github.com/ServeurpersoCom/omnivoice.cpp/pull/24).
They should be reviewed there before
VoiceStudio changes its source pin or Intel build defaults.

VoiceStudio already has an [OmniVoice GGUF engine](omnivoice-gguf.md).
Integration should extend that engine rather than add an engine named after
one user's GPU or bake in device-memory numbers. A production contribution
also needs:

- an explicit Intel-Metal build opt-in, preserving the current CPU build;
- verified device selection and runtime-reported hardware, not the parent's
  PyTorch hardware detection or a hard-coded Radeon model;
- `--no-fa` and isolated codec serialization only for the verified opt-in;
- existing model acceptance checks, synthetic-audio marking and fallback;
- reference/transcript parity and checks on other GPU families before wider
  support is claimed.

This is a documented integration boundary, not a claim that those application
changes are already shipped.

## Docker versus native compute

Docker Desktop runs a Linux VM on macOS; this experiment did not expose the
Mac's Metal GPU inside that VM. VoiceStudio's UI/API ran in Docker while an
authenticated host-side service ran native Metal inference. That custom bridge
is not bundled with VoiceStudio. Binding to loopback alone is insufficient:
any future resident service integration must authenticate every request and
must not expose registered reference voices to unrelated browser origins.

The existing [resident mode](omnivoice-gguf.md#resident-server)
remains separate from this experimental path. Do not enable a public listener
or advertise the Mac GPU as available in the Docker backend based only on the
host inventory.

## Performance expectations

GPU assignment is not real-time performance. Local matched 16-step tests
produced about 3.3 seconds of audio in roughly 40–45 seconds. Keeping the
model loaded saved several seconds, but most time remained speech-model
inference. These observations are not cross-device benchmarks or guarantees.
Keep quality settings fixed when comparing implementations; reducing the
number of refinement steps is a separate quality/speed trade-off.
