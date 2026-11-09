# CosyVoice 3 GGUF on Vulkan

VoiceStudio has an optional native CosyVoice 3 backend for machines where the
official Python/PyTorch route is not a good fit. It runs the community
[`Lourdle/cosyvoice.cpp`](https://github.com/Lourdle/cosyvoice.cpp) GGML port as
a local server. The tested AMD route is a Radeon RX 6700 XT using Vulkan. This
confirms that card and build; it is not a guarantee for every AMD GPU.

This is a separate engine named **CosyVoice 3 GGUF**. The existing Python
**CosyVoice 3** engine and VoiceStudio default do not change. It requires a
reference recording and its transcript, supports CosyVoice 3's nine languages,
and passes free-form instructions such as “soft, warm, and breathy” to the
model. The native route also accepts speaking-speed and seed controls. It returns 24 kHz speech. It does not provide text-only voice design or
promise ElevenLabs feature parity. Quantized output may sound muffled on some
lines; review the upstream model card and listen to the actual renders.

## Build the native runtime

The executable is not bundled or downloaded by VoiceStudio. Build the pinned
community source yourself and retain its notices and all GGML/Vulkan/ONNX
Runtime notices. On Windows, install Git, CMake, Visual Studio C++ build tools,
and the Vulkan SDK, then from a Developer PowerShell:

```powershell
git clone --recursive https://github.com/Lourdle/cosyvoice.cpp.git
cd cosyvoice.cpp
git checkout 67d45feb2f654bbc66cca25bff78464b3ec08fa1
git submodule update --init --recursive
cmake -S . -B build -DGGML_VULKAN=ON -DCOSYVOICE_NO_ICU=ON -DCOSYVOICE_SERVER_NO_WEBUI=ON
cmake --build build --config Release --parallel
```

Use the build output containing `cosyvoice-server.exe` and `cosyvoice-cli.exe`.
Set `OMNIVOICE_COSYVOICE_GGUF_DIR` to that output folder, or set the individual
`OMNIVOICE_COSYVOICE_GGUF_SERVER` and `OMNIVOICE_COSYVOICE_GGUF_CLI` variables.
The engine will report which native devices the runtime actually exposes.

## Install and select the model

Open **Settings → Models** and install **CosyVoice 3 0.5B Q4_K_M (native
GGUF/Vulkan)**. The entry is pinned to an immutable model revision and installs
the GGUF and two ONNX frontend assets. Generation never downloads a model or
runtime. Existing local model files can instead be selected with
`OMNIVOICE_COSYVOICE_GGUF_MODEL` and `OMNIVOICE_COSYVOICE_GGUF_FRONTEND`. A
set override must point at real files: a missing model or frontend file is an
error, never a silent fallback to the installed assets.

The backend selector is `OMNIVOICE_COSYVOICE_GGUF_BACKEND`:

| Value | Behavior |
|---|---|
| `auto` (default) | Select a Vulkan device reported by the native runtime; otherwise use CPU and disclose the fallback. |
| `Vulkan0` | Require that exact detected device. Missing Vulkan is an error, never a silent CPU fallback. |
| `CPU` | Force CPU execution. |

For the tested AMD workstation, set `OMNIVOICE_COSYVOICE_GGUF_BACKEND=Vulkan0`
and confirm the engine details list `Vulkan0` with `AMD Radeon RX 6700 XT`.
Voice prompts are cached locally by reference-audio content plus transcript;
requests and profile changes are serialized so a render cannot use another
voice's prompt. The server binds to loopback, uses a per-process API key, and is
stopped when the engine unloads or stops responding; a request the server
rejects (an HTTP error) is reported with its status and leaves it running.

The pinned runtime accepts its API key only as a command-line argument, so
other accounts on the same computer that can list process arguments can read
it while the server runs. The key is regenerated on every launch and the port
accepts loopback connections only; on a shared multi-user machine, prefer
another engine.

## Model and runtime terms

The upstream model card reports Apache-2.0. VoiceStudio records the model as
unreviewed: component provenance, commercial inference, output terms, and
redistribution obligations have not been cleared. The runtime is an unofficial
community port; retain its MIT and bundled third-party notices. These notices
do not grant rights to cloned reference voices. Use only recordings you have
permission to process. See the [model inventory](../licensing/model-inventory.md)
and [upstream model card](https://huggingface.co/Lourdle/Fun-CosyVoice3-0.5B-2512-GGUF/tree/ab01740bbbfcc059fd748163787554f7c8977e64).
