# Implementation Spec — TASK #21: GPU compatibility matrix + preflight (no silent CPU fallback)

## TL;DR

`gpu_compat` exists today as **static class metadata** on each `TTSBackend` subclass (`backend/services/tts_backend.py:103` ABC default + per-engine overrides), surfaced through `tts_backend.list_backends()` (`:1151`) → `/engines` (`backend/api/routers/engines.py:38`) → `EngineCompatibilityMatrix.jsx` (chips at `:313-317`) as decorative chips. Nothing cross-references those declared targets against the host's **actual** compute device. The result: MLX-Audio advertises `mps` on Linux/Windows where it can never run (issue #390 — `is_available()` at `tts_backend.py:579` is import-guard-only, no MPS check); CUDA-only engines silently fall to CPU via `get_best_device()` (`model_manager.py:195`); the ASR and LLM registries emit no `gpu_compat` at all (`asr_backend.list_backends():991`, `llm_backend.list_backends():169` emit only `{id, display_name, available, reason}`); and there is no warning at engine-select (`engines.py:270`) or any synth time (REST `generation.py:286`, OpenAI-compat `openai_compat.py:252`, WS `tts_stream.py:87`) when the active engine cannot use the user's GPU.

This task builds a single canonical device-family probe (`backend/core/device_caps.py`), promotes a meaningful routing attribute to ASR and LLM (GPU-family for ASR; **`n/a`/`network` for LLM**, since the only LLM backends are remote/off — §3a), computes a per-engine **effective device** (intersection of declared targets and host capabilities) plus a **routing status** (`accelerated` / `cpu_fallback` / `cpu_only` / `unavailable`), surfaces that in the `/engines` payload and the existing matrix UI, adds a `gpu_routing` block to `/setup/preflight` (`wizard.py:203`) and `/system/diagnose` (`core/diagnose.py`), and raises an explicit, non-silent warning/error at engine-select and **every** synth entry point when the active engine cannot use the user's GPU.

## Problem

1. **`gpu_compat` is unverified metadata, not routing.** `backend/services/tts_backend.py:103` declares it on the ABC (`gpu_compat: tuple[str, ...] = ("cpu",)`); subclasses override:
   - VoiceStudio `:172` `("cuda","mps","cpu")`
   - VoxCPM2 `:257` `("cuda","mps","cpu")`
   - MossTTSNano `:366` `("cuda","cpu")`
   - KittenTTS `:460` `("cpu",)`
   - MLX-Audio `:553` `("mps","cpu")`
   - CosyVoice `:682` `("cuda","cpu")`
   - GPTSoVITS `:846` `("cuda","cpu")`
   - SherpaOnnx `:955` `("cuda","cpu")`
   - gguf `engines/omnivoice_gguf/backend.py:296` `("cuda","mps","cpu")`
   - Supertonic-3 `engines/supertonic3/backend.py:75` `("cpu",)`
   - **IndexTTS2 `engines/indextts/__init__.py:41` declares NO `gpu_compat`** → inherits the ABC default `("cpu",)` even though it is a CUDA-class subprocess engine. This is a latent bug surfaced by this task: it currently advertises CPU-only.

   `tts_backend.list_backends()` (`:1151`) copies the tuple verbatim into the API (`:1217`, `list(getattr(cls, "gpu_compat", ("cpu",)))`). The frontend (`EngineCompatibilityMatrix.jsx:313-317`) renders one chip per declared target. None of this consults the actual host device.

2. **Silent CPU fallback.** `get_best_device()` (`model_manager.py:195-233`) returns the best available device, and engines just `.to(device)`. A CUDA-only engine on a Mac, or any engine on a driver-broken / arch-mismatched NVIDIA box, silently runs on CPU (~10× slower) with no signal in Settings or at synth. `_check_device()` (`diagnose.py:63`) only WARNs `"cpu (no GPU acceleration detected)"` (`:80-85`) globally — it never says "your active engine *wanted* CUDA but got CPU." There are in fact **two distinct "CUDA present but unusable" modes** the probe should surface: driver-too-old (`wizard.py:128-138`, gated by `_MIN_NVIDIA_DRIVER_LINUX/_WINDOWS` (525.60.13 / 528.33, CUDA 12.x minor-version compat)) and SM-arch-mismatch (`model_manager.check_device_compatibility():167-192`, which checks `torch.cuda._get_arch_list()` against the device's `sm_NN` tag — NOT the driver version, returns `(compatible, warning)` and is only *logged* at `get_best_device():206-209`).

3. **MLX import-guards only (#390).** `MLXAudioBackend.is_available()` (`tts_backend.py:579`) returns `True` whenever `import mlx_audio` succeeds (catching `ImportError/OSError/RuntimeError`), with **no MPS / platform check** — so a Linux/Windows wheel (or a stray install) reports the engine as available and advertises `mps`. The class docstring (`:535-547`) already *claims* "Apple Silicon only … Skipped entirely on Linux/Windows/mac-Intel," but the code doesn't enforce it. The ASR sibling `MLXWhisperBackend.is_available()` (`asr_backend.py:507`) *does* gate on `torch.backends.mps.is_available()` (`:510`), proving the two backends disagree on the same constraint. There is no shared rule.

4. **ASR/LLM have no `gpu_compat`, and ASR/LLM `list_backends()` are thin.** `asr_backend.ASRBackend` (`:37`) has only `id`/`display_name`/`is_available`/`transcribe`/`unload` — no `gpu_compat`. `asr_backend.list_backends()` (`:991-1001`) emits only `{id, display_name, available, reason}` — and, unlike TTS, **applies no HF-token masking** (no `_mask_hf_tokens` in `asr_backend.py`; `reason` is emitted unmasked at `:999`) and emits no `install_hint`/`last_error`/`isolation_mode`. The same is true of `llm_backend.list_backends()` (`:169-179`). The matrix already handles the missing fields defensively (`types.ts:14-31` comment + optional fields; `EngineCompatibilityMatrix.jsx:89-91` defaults `gpu_compat` to `['cpu']`), so ASR rows currently *lie* by showing CPU-only.

5. **No single platform-aware truth — three (really four) detectors disagree.** There are already three host-detection code paths plus a sandbox surface, none of which distinguish ROCm from CUDA in a way the matrix can see:
   - `model_manager.get_best_device()` (`:195-233`) — returns a device *string*; ROCm reports through `torch.cuda` so it returns `"cuda"` for both NVIDIA and AMD. Has real side-effects: `_configure_rocm_if_needed(torch)` (`:139-162`, sets `HSA_OVERRIDE_GFX_VERSION` from the `_ROCM_GFX_OVERRIDES` map at `:130-135`), a DirectML branch (`:220-227`, returns `str(torch_directml.device(0))` — **not** a `DeviceFamily` enum value), and an XPU/IPEX branch (`:211-218`). Priority order in the code: CUDA/ROCm → XPU → DirectML → MPS → CPU.
   - `engines/omnivoice_gguf/hardware_probe.detect_capabilities()` (`:72-124`) — the closest existing probe, but gguf-private and **explicitly tags ROCm as `cuda`** (the `if torch.cuda.is_available()` branch at `:96-110` returns `backend="cuda"`; docstring `:81-83` says so). So "rocm" never surfaces as a distinct routing target anywhere.
   - `setup/wizard._detect_gpu()` (`:91-181`) — *does* distinguish nvidia/amd/apple/unknown (via `nvidia-smi`/`rocm-smi` shell-outs + `torch.version.hip` at `:148-150`), but lives in a separate code path from `gpu_compat` and returns a `vendor`/`backend`/`available`/`notes` dict, not a routing decision. Note its existing logic already sets `available=False` for NVIDIA when `driver < _MIN_NVIDIA_DRIVER` (`:128-138`) and has a "torch sees CUDA but no smi" Docker/WSL fallback branch (`:166-181`).
   - (`hardware_probe`'s own docstring at `:3` notes it "extends `backend/services/gpu_sandbox.py`'s existing CUDA/MPS/ROCm/CPU detection" — `gpu_sandbox.py` is a fourth surface, used only for sandbox availability at `:150`. `device_caps` becomes the single source these delegate to; see §1.)

## Goal / Non-goals

### Goals
- One canonical, cheap, side-effect-free device probe (`backend/core/device_caps.py`) returning the host's real compute family (`cuda` / `rocm` / `mps` / `xpu` / `cpu`) plus VRAM/driver facts, shared by preflight, diagnose, matrix, and every synth path.
- Per-engine **effective routing**: given declared `gpu_compat` ∩ host capability, compute `{effective_device, routing_status, routing_reason}` and surface it on every TTS + ASR `/engines` entry (LLM gets a `n/a` routing — §3a).
- Promote `gpu_compat` to `ASRBackend` with accurate per-engine values; give `IndexTTS2Backend` a real (non-default) `gpu_compat`.
- Fix #390: MLX backends report `available=False` (and never advertise `mps`) on non-Apple-Silicon hosts, via a shared platform-gate helper.
- **No silent CPU fallback**: at engine-select (`engines.py:270`) and at **all** synth entry points (REST `generation.py:286`, OpenAI-compat `openai_compat.py:252`, WS `tts_stream.py:87`), when the active engine would land on CPU despite declaring an accelerator the host has, OR cannot run on this host at all, raise an explicit, structured warning/error.
- Surface routing in the matrix UI as "will run on **your** machine" (effective device badge + reason), and in `/setup/preflight` + `/system/diagnose` as a `gpu_routing` block.

### Non-goals
- Changing how any engine actually loads weights or selects its device internally (routing is **advisory metadata + preflight gating**, not a rewrite of loaders). `get_best_device()`'s device-string contract and side-effects (`_configure_rocm_if_needed`, DirectML, XPU) are preserved. Backward-compatible with on-disk model state per CLAUDE.md.
- Forcing an engine off CPU when the user genuinely has no GPU — that path stays a WARN, fully functional (local-first guarantee).
- New native deps (keyring, etc.) — uses `torch`, `psutil`, `platform`, already pinned.
- Auto-installing GPU wheels or switching engines automatically.
- **No DB schema change and no migration.** This task touches no model under `core/db.py`/`core/job_store.py` and adds no alembic revision; all persistence-shaped state (engine pick) reuses the existing `prefs.json` key path. The "DB schema + migration" dimension of the API/data-shapes lens is therefore **N/A by design** — recorded explicitly so a reviewer doesn't go looking for a migration that should not exist (see §Constraints, backward-compatible data; §API).

## Design

### 1. Canonical device probe — `backend/core/device_caps.py` (new)

A small, torch-lazy, cached module that is the single source of truth for "what can this host accelerate on." Distinguishes ROCm from CUDA (unlike `hardware_probe`).

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

DeviceFamily = Literal["cuda", "rocm", "mps", "xpu", "cpu"]

@dataclass(frozen=True)
class HostCaps:
    family: DeviceFamily                          # best available accelerator family, else "cpu"
    available_families: tuple[DeviceFamily, ...]   # everything usable, ALWAYS includes "cpu"
    device_name: str = ""                          # "NVIDIA RTX 4090" / "Apple Silicon (MPS)" / "" (device 0)
    vram_gb: float = 0.0                           # CUDA/ROCm total; MPS = ram/2; 0 for cpu/xpu
    driver: str | None = None                      # nvidia driver string, rocm hip version, else None
    notes: tuple[str, ...] = ()                    # e.g. "driver below min", "arch not in torch build",
                                                   #      "torch built without ROCm", DirectML/multi-GPU markers
    probe_ok: bool = True                          # False iff torch could not be imported/queried (§1a)

def detect_host_caps() -> HostCaps:
    """Cached per-process. Never raises (§1a). Makes no network call. Kernel-free on cold start."""

def refresh() -> HostCaps:
    """Clear the cache and re-probe; returns fresh caps. TEST-ONLY — not wired to any endpoint (§1a)."""

def mlx_supported() -> tuple[bool, str]:
    """(ok, reason). ok=True ONLY on Apple Silicon + torch MPS available (§3). No regex."""
```

**Exact field domains (pinned):**
- `family` ∈ `{"cuda","rocm","mps","xpu","cpu"}` (the `DeviceFamily` enum exactly; DirectML is **not** a member — it maps to `"cpu"` + a note, §1a/§2).
- `available_families` is a deduplicated, order-stable tuple; **`"cpu"` is always present** (invariant — assert in tests). On a CUDA host it is `("cuda","cpu")`; on a ROCm host `("rocm","cpu")`; on Apple Silicon with MPS `("mps","cpu")`; on XPU `("xpu","cpu")`; on a degraded/CPU host `("cpu",)`.
- `vram_gb` is a non-negative `float` (GB, not MB — `hardware_probe` reports MB internally; `device_caps` converts to GB here).
- `driver` is the raw string from `nvidia-smi`/`torch.version.hip` or `None` (never a parsed int — the int parse stays local to the note logic).
- `notes` is a tuple of author-controlled English strings (never user input). Empty tuple on a clean accelerated host.
- `probe_ok` defaults `True`; `False` only in the torch-unimportable branch (§1a).

- **ROCm detection:** `torch.cuda.is_available()` **and** `getattr(torch.version, "hip", None)` → `family="rocm"` (mirrors `wizard.py:148-150`). Pure-CUDA → `family="cuda"`.
- **`xpu`** via Intel IPEX (mirror `model_manager.py:211-218`: `import intel_extension_for_pytorch` + `torch.xpu.is_available()`); always include `"cpu"` in `available_families`.
- **VRAM:** CUDA/ROCm total via `torch.cuda.mem_get_info()` (mirror `hardware_probe.py:98-99`, with the same `except → 0` guard at `:100-105`), converted bytes→GB; MPS = `psutil.virtual_memory().total / 2` (mirror `hardware_probe.py:116-117`), bytes→GB; 0 for cpu/xpu (XPU VRAM query is unreliable across IPEX versions — leave 0 + a note rather than guess).
- **Driver/incompatibility notes:** reuse the `_min_nvidia_driver()` (525.60.13 Linux / 528.33 Windows) driver-version logic from `wizard.py:74,128-138` (extract a shared `_nvidia_driver_ok()`), **and** the SM-arch-list check from `model_manager.check_device_compatibility():167-192` (which returns `(compatible, warning)` and already handles the `sm_NN`/`compute_NN` arch-list comparison). When CUDA is present but either check fails, **keep `family="cuda"` and emit a `note`** (recommended, documented choice), because `get_best_device()` itself still returns `"cuda"` in that case (it only *logs* the warning at `:206-209`); `resolve_routing` then keeps the engine `accelerated`-declared while `_check_device`/preflight surface the warning. (Rationale for not flipping to "not-CUDA-usable": doing so would silently push the engine onto CPU at synth in a way that contradicts what `get_best_device()` actually returns to the loader — the probe and loader must never disagree.)
- **No-regex constraint on driver/note construction (CodeQL):** the driver-version parse reuses `wizard.py`'s `int((driver or "0").split(".")[0])` split (no regex on a user/driver-influenced string). The SM-arch comparison reuses `check_device_compatibility()`'s existing list-membership logic. **No new regex is introduced in `device_caps.py`** — see §Constraints (CodeQL).
- **`get_best_device()` (`model_manager.py:195`) refactor:** its family decision delegates to `detect_host_caps().family`, while keeping its DirectML branch (`:220-227`) and ROCm `HSA_OVERRIDE_GFX_VERSION` side-effect (`_configure_rocm_if_needed`, `:139-162`) so the probe and the loader can never disagree. (Note: `get_best_device()` returns a device *string* including DirectML's `str(torch_directml.device(0))`; `detect_host_caps().family` is the coarser *family* enum — keep both, the probe is the source of the family decision only.) **The probe must NOT call `_configure_rocm_if_needed`** (that's a mutating side-effect setting an env var) — only `get_best_device()` does, preserving its existing call order. The probe reads, the loader writes.
- **`hardware_probe.detect_capabilities()` (`:72`)** keeps its `HardwareCapabilities`/`ComputeClass` VRAM-bucketing API (`_bucket()` at `:54-69`, `quant_map.json` keyed by `compute_class`) but rebases its `backend`/`vram_mb` detection on `device_caps`. ROCm is now reported as `rocm` in `HostCaps`; `hardware_probe`'s own `backend` field keeps its `Literal["cuda","mps","rocm","cpu"]` type — add a back-compat note that gguf quant selection still buckets `rocm` the same as `cuda` (VRAM-driven, not silicon-driven), so no quant-selection regression. (`hardware_probe` reports VRAM in MB; `device_caps` reports GB — the rebase multiplies/divides explicitly, no implicit unit change in `hardware_probe`'s public field.)

### 1a. Probe edge cases, empty/error paths, and the degradation contract (COMPLETENESS)

`detect_host_caps()` must **never raise** to its callers — `/engines`, `/setup/preflight`, every synth path, and `/system/diagnose` all call it, and a probe exception must degrade gracefully, not 500 the endpoint or brick synthesis. This degrade-not-die contract is also a **local-first** requirement: the app must stay fully functional with no GPU and even with a broken torch (see §Constraints). Every branch below is enumerated and handled explicitly. **The "Result" column is the exact `HostCaps` field set produced.**

| Condition | Detection | Result (exact `HostCaps`) |
|---|---|---|
| **torch not importable** (`ImportError`) | the lazy `import torch` raises | `HostCaps(family="cpu", available_families=("cpu",), device_name="", vram_gb=0.0, driver=None, notes=("torch not importable; treating host as CPU-only",), probe_ok=False)`. All engines route as if on a CPU-only host. |
| **torch present but `torch.cuda.is_available()` raises** (broken CUDA init, e.g. forked-process / driver crash) | wrap in try/except | swallow, treat CUDA as absent, append note `"CUDA init raised: <ExcType>"`; continue probing MPS/XPU. `probe_ok` stays `True`. |
| **`torch.version.hip` attribute missing** | `getattr(torch.version, "hip", None)` | falls through to pure-CUDA classification — no crash. |
| **CUDA available, 0 devices** (`torch.cuda.device_count() == 0`) | check count before indexing device 0 | treat as no CUDA; append note `"CUDA reports available but device_count==0"`. Prevents `get_device_name(0)` / `mem_get_info()` `IndexError`. |
| **Multi-GPU host** | always index device **0** | `device_name`/`vram_gb`/`driver` reflect device 0 only; append note `"N GPUs detected; routing reflects device 0"` when `device_count() > 1`. Routing is family-level, not per-device, so this is advisory only. |
| **`mem_get_info()` raises** | reuse `hardware_probe`'s `except → 0` guard (`:100-105`) | `vram_gb=0.0` + note `"VRAM query failed"`. Never blocks family classification. |
| **`get_device_name(0)` raises** | try/except | `device_name=""`; family still set. |
| **`_get_arch_list()` empty or missing** | `getattr(..., lambda: [])()` (mirrors `check_device_compatibility:179`) | SM-arch check is skipped (treated as compatible), exactly as the existing code does — no false `unavailable`. |
| **`get_device_properties().gcnArchName` missing** (ROCm/older torch) | `getattr(props, "gcnArchName", "")` (mirrors `_configure_rocm_if_needed:158`) | no crash; ROCm still classified via `torch.version.hip`. |
| **NVIDIA driver string unparseable** (e.g. `""`, `"N/A"`) | `int((driver or "0").split(".")[0])` in a try (mirrors `wizard.py:131-138`) | driver check skipped, no note added, family stays `cuda`. |
| **DirectML present** (`torch_directml.device_count() > 0`) | optional import | DirectML is **not** a `DeviceFamily` enum member. Set `family="cpu"` **but** append note `"DirectML device present (Windows GPU); torch-family probe treats as non-accelerated"`. `available_families` is **not** augmented with a synthetic value (keep the enum clean). See §2 for how `resolve_routing` avoids spurious `unavailable` on DirectML hosts. |
| **XPU/IPEX present** | mirror `model_manager.py:211-218` | `family="xpu"`, `available_families=("xpu","cpu")`, `vram_gb=0.0` (see above). |
| **psutil missing** (MPS VRAM path) | try/except | `vram_gb=0.0` + note; MPS family still set. |
| **`platform.machine()` returns unexpected value** (e.g. `i386` on Rosetta, `x86_64` Mac) | `mlx_supported()` (§3) gates strictly on `darwin` + `arm64` | mac-Intel correctly classified as non-MPS for MLX; native MPS (`torch.backends.mps.is_available()`) still reported in `available_families` if torch sees it (Apple Silicon non-MLX engines like VoiceStudio/VoxCPM2 still route to MPS). |

- **Caching & refresh contract:** `detect_host_caps()` caches the `HostCaps` in a module global (`functools.lru_cache(maxsize=1)` or a module-level sentinel) computed **once per process**. Host compute capability does not change at runtime in any supported flow (no GPU hot-plug in the desktop app; switching the *engine* in Settings does **not** change host caps, so an engine switch must **not** trigger a re-probe — the routing is recomputed from the same cached caps against the newly-active engine's `gpu_compat`). `refresh()` exists **only** for tests (to re-probe after monkeypatching torch) and is **not** wired to any endpoint. Document explicitly: nothing in the running app calls `refresh()`. `probe_ok=False` results are cached too (we do not retry torch import on every request — if torch is unimportable once, it stays unimportable for the process).
- **Cost guard:** the probe must stay kernel-free on cold start (driver/sysctl queries only, no tensor allocation), per the Risk section. The `_get_arch_list()` and `get_device_capability()` calls are metadata-only and cheap.

### 2. Routing resolver — `backend/services/engine_routing.py` (new)

Pure function, no model load. **Typed return (pinned — replaces the prior bare `-> dict`):**

```python
from __future__ import annotations
from typing import Literal, TypedDict
from backend.core.device_caps import HostCaps, DeviceFamily

RoutingStatus = Literal["accelerated", "cpu_fallback", "cpu_only", "unavailable", "n/a"]

class RoutingResult(TypedDict):
    effective_device: str          # a DeviceFamily value, "cpu", or (LLM only) "network"
    routing_status: RoutingStatus
    routing_reason: str | None     # always str for cpu_fallback/unavailable; may be caveat str
                                   # for accelerated; None for cpu_only unless DirectML note applies

def resolve_routing(gpu_compat: tuple[str, ...], caps: HostCaps) -> RoutingResult: ...
```

**Exact value domains (pinned):**
- `effective_device` is one of `{"cuda","rocm","mps","xpu","cpu"}` for TTS/ASR (never `"network"` — `"network"` is produced only by the LLM list builder in §3a, which does **not** call `resolve_routing`).
- `routing_status` is one of the five `RoutingStatus` literals; `resolve_routing` **never** returns `"n/a"` (that value is injected by the LLM builder, §3a). So the resolver's effective range is `{"accelerated","cpu_fallback","cpu_only","unavailable"}`.
- `routing_reason` is pre-`scrub_text` here (the caller scrubs at serialization — §5/§7); the function itself returns the raw author-controlled string.

Rules (deterministic, host-aware), evaluated **in order** so the first match wins:
1. `gpu_compat` is empty `()` → reserved for LLM only; `resolve_routing` is not called for LLM. If it is ever called with `()`, return `{"effective_device":"cpu", "routing_status":"cpu_only", "routing_reason":"engine declares no compute targets"}` defensively rather than crash.
2. Host family ∈ declared targets **and host family ≠ cpu** → `{"effective_device": caps.family, "routing_status":"accelerated", "routing_reason": <None, or caveat string if caps carries a driver/arch note — see edge rules>}`.
3. Host family is an accelerator the engine does **not** support, but engine supports `cpu` → `{"effective_device":"cpu", "routing_status":"cpu_fallback", "routing_reason":"engine has no {host.family} path; running on CPU"}`. **This is the no-silent-fallback signal.**
4. Host is cpu-only (`caps.family == "cpu"`) and engine supports `cpu` → `{"effective_device":"cpu", "routing_status":"cpu_only", "routing_reason": None}` (benign; matches a no-GPU machine — **must not** warn, **must not** block). Exception: DirectML note present → `routing_reason` set per the DirectML edge below.
5. Engine declares only accelerators the host lacks **and** no `cpu` in the tuple → `{"effective_device": <first declared target>, "routing_status":"unavailable", "routing_reason":"requires {targets}; this host has {host.family}"}`.

Specific edge rules (all spelled out — no hand-waving):
- **ROCm-not-in-set:** an engine listing `cuda` but not `rocm` on a ROCm host → falls into rule 3 → `cpu_fallback` with reason `"declares CUDA only; ROCm not in its compat set"` (engines that genuinely run on ROCm-via-HIP must add `"rocm"` to their tuple — see §4).
- **DirectML host** (`caps.family == "cpu"` with a DirectML note from §1a): because the probe reports `family="cpu"`, a `cpu`-supporting engine resolves to `cpu_only`, **not** `unavailable` — so DirectML-only Windows users never get a spurious 400/`unavailable`. `resolve_routing` checks `caps.notes` for the DirectML marker substring and, when present, sets `routing_reason` to `"DirectML GPU present; engine routes via torch CPU path (DirectML acceleration not wired into routing)"` so the UI explains the neutral badge rather than implying "no GPU." Status stays `cpu_only` (neutral, never blocks). **(This is also a cross-platform-parity requirement: the Windows DirectML edge must not produce a worse user-visible outcome than macOS/Linux — see §Constraints.)**
- **XPU host:** an engine that does **not** list `xpu` (none do today) but lists `cpu` → `cpu_fallback` on an XPU host (reason `"engine has no XPU path; running on CPU"`). An engine listing only non-XPU accelerators with no cpu → `unavailable`. (No engine currently lists `xpu`; this is defined for forward-compat.)
- **probe_ok=False** (torch unimportable, §1a): `caps.family == "cpu"` → every cpu-supporting engine is `cpu_only` (matches a genuine no-GPU host), and any accelerator-only-no-cpu engine is `unavailable`. The probe's degraded state never *invents* acceleration.
- **Driver-too-old / arch-mismatch CUDA host** (note present, `family` still `cuda` per §1): a `cuda`-listing engine resolves to `{"effective_device":"cuda","routing_status":"accelerated","routing_reason":"CUDA selected, but: <probe note>"}` (matching what the loader will actually attempt). Example interpolation: `"CUDA selected, but: driver 520 < 525.60.13 required — may fail at kernel launch"`. The matrix badge stays `accelerated` tone but the reason surfaces the caveat; preflight/diagnose escalate it to a WARN (see §6). This is the one case where `accelerated` carries a non-`None` `routing_reason`.
- `"n/a"` is reserved for LLM (§3a) — `resolve_routing` is not called for LLM family.

`routing_reason` is **always a non-empty `str` for `cpu_fallback`/`unavailable`**, may be a caveat `str` for `accelerated` (driver/arch note) else `None`, and is `None` for `cpu_only` unless a DirectML note applies. **All reason strings are author-controlled English literals (interpolating only family names / device names / driver versions) — never raw user input — but are nonetheless serialized through `scrub_text` (§5) because the interpolated `device_name`/note can carry a home path or, in the availability-raised case, an exception message.**

### 3. Promote `gpu_compat` to ASR, fix #390, fix IndexTTS2

- `ASRBackend` (`asr_backend.py:37`) gets `gpu_compat: tuple[str, ...] = ("cpu",)` on the ABC; each subclass declares real values:
  - WhisperX (`:61`) / FasterWhisper (`:382`) → `("cuda","rocm","cpu")` (CTranslate2 supports CUDA; ROCm via HIP build).
  - MLXWhisper (`:497`) → `("mps","cpu")`.
  - PyTorchWhisper (`:566`) → `("cuda","rocm","mps","cpu")`.
  - NeMoASR (`:632`) / FunASR (`:886`) → `("cuda","cpu")`.
  - MoonshineASR (`:741`) → `("cpu",)`.
- `IndexTTS2Backend` (`engines/indextts/__init__.py:41`, `id="indextts2"` at `:78`) gets an explicit `gpu_compat = ("cuda","cpu")` (or `+rocm` per §4 audit) so it stops advertising CPU-only via the inherited default.
- **#390 fix — shared platform gate** in `core/device_caps.py`:
  ```python
  def mlx_supported() -> tuple[bool, str]:
      """Return (ok, reason). ok=True ONLY on Apple Silicon
      (sys.platform == "darwin" and platform.machine() == "arm64")
      with torch MPS available. Mirrors the wizard's Apple branch
      (wizard.py:99-105) and the ASR MLX gate (asr_backend.py:510).
      `reason` explains the False path. Gates on exact-string equality
      (sys.platform / platform.machine), NOT regex — no CodeQL
      py/polynomial-redos surface (§Constraints)."""
  ```
  **Exact return values (pinned) for `mlx_supported()`:**
  - Apple Silicon + torch MPS available → `(True, "")` (reason empty on the True path).
  - mac-Intel (`darwin` + `x86_64`) → `(False, "MLX requires Apple Silicon; this Mac is Intel")`.
  - Apple Silicon but `torch.backends.mps.is_available()` False (e.g. torch built without MPS) → `(False, "Apple Silicon detected but torch MPS unavailable; reinstall torch with MPS support")`.
  - Apple Silicon but torch unimportable → `(False, "torch not importable; cannot confirm MPS")` (does **not** crash; conservatively unavailable).
  - Linux/Windows (any non-`darwin` `sys.platform`), incl. with a stray `mlx_audio` wheel installed → `(False, "MLX requires Apple Silicon; this host is {sys.platform}/{platform.machine()}")` **before** the package import, so the import is never attempted and `available=False`.

  `MLXAudioBackend.is_available()` (`tts_backend.py:579`) and `MLXWhisperBackend.is_available()` (`asr_backend.py:507`) both call it **first**, before importing the package: `ok, why = mlx_supported(); if not ok: return (False, why)`. The contract of both `is_available()` methods is unchanged — they still return `tuple[bool, str | None]` — so a stray non-Apple install no longer reports available or advertises `mps`. (The ASR backend's existing inline MPS check at `:510` is replaced by the shared call to keep one rule.) `MLXAudioBackend.gpu_compat` stays `("mps","cpu")` but on a non-Apple host `resolve_routing` would return `cpu_fallback` (mps not in available families, cpu is) — however because `is_available()` returns `False` there, the engine is already filtered as unavailable in select/synth; the matrix shows `available:false` and the routing badge is suppressed/`unavailable`-consistent. Document this interaction so the matrix doesn't show a confusing "cpu_fallback + unavailable" pairing: **when `available` is false, the matrix renders the availability state and dims/omits the routing badge** (see §8).
  - **Cross-platform parity note:** MLX is intrinsically Apple-Silicon-only — it is **not** a default-everywhere feature, so its platform-gating does **not** violate the "defaults must work on every platform" rule. The *default-everywhere* behavior here is the **routing/no-silent-fallback machinery itself**, which runs identically on all three OSes; MLX is correctly fenced behind its hardware capability via `mlx_supported()`, the same way a macOS-only feature would be fenced behind an opt-in (§Constraints).

### 3a. LLM family — routing is `n/a` (not a GPU family)

**Correction vs. prior draft:** the LLM registry (`llm_backend.py:163-166`) contains only `OpenAICompatBackend` (`:60`, calls out to OpenAI/Ollama/LM Studio via the `openai` client — `is_available()` gates on `TRANSLATE_BASE_URL`/`TRANSLATE_API_KEY` at `:73-83`) and `OffBackend` (`:141`, a no-op). **Neither runs a local model on the user's GPU.** Promoting a GPU-family `gpu_compat` to `LLMBackend` would be meaningless. Instead:
- Give `LLMBackend` (`:37`) a class attribute `gpu_compat: tuple[str, ...] = ()` (empty) and have `llm_backend.list_backends()` (`:169`) emit, **per entry, as literal constants (NOT via `resolve_routing`)**: `"effective_device": "network"`, `"routing_status": "n/a"`, `"routing_reason": None` for both backends. The matrix renders LLM rows with a neutral "remote / off" badge rather than a device chip.
- This keeps the `/engines` payload shape uniform across families (all three carry the four routing keys) without asserting a false GPU claim.
- **Edge:** `select_engine` for the LLM family (`family=="llm"`) must **skip** the routing gate entirely (`routing_status == "n/a"` is never `unavailable`/`cpu_fallback`), exactly as §7 states. Diagnose/preflight compute routing only for the active **TTS** engine (and, for the dub note, the active ASR engine) — never for LLM.
- **Local-first note:** `OpenAICompatBackend` is the only outbound-network surface among engines, and it is **opt-in by configuration** (`is_available()` is False until the user sets `TRANSLATE_BASE_URL`/`TRANSLATE_API_KEY`). This task adds **no** new network call — `effective_device:"network"` is a *label*, not a probe; `detect_host_caps()` never touches the network (§Constraints, local-first).

### 4. Per-engine `rocm` declarations
Audit the CUDA-listing TTS engines for genuine ROCm support and update tuples (exact anchors):
- VoiceStudio (`tts_backend.py:172`), VoxCPM2 (`:257`), MossTTSNano (`:366`), CosyVoice (`:682`), GPTSoVITS (`:846` — server-side, host-agnostic), Sherpa-ONNX (`:955` — onnxruntime ROCm EP), gguf (`engines/omnivoice_gguf/backend.py:296` — Vulkan/ROCm via GGML), IndexTTS2 (`engines/indextts/__init__.py`). Add `"rocm"` where the path actually works; leave a code comment citing the upstream support claim. Engines that are CUDA-only stay CUDA-only and will correctly show `cpu_fallback` on ROCm hosts. Supertonic-3 (`engines/supertonic3/backend.py:75`) is ONNX-CPU today (`("cpu",)`) — leave as-is unless an ONNXRuntime CUDA/ROCm EP is wired in.

### 5. Wire routing into `list_backends()`
Both `tts_backend.list_backends()` (`:1151`) and `asr_backend.list_backends()` (`:991`) gain, per entry, **these four keys with exactly these JSON types**:
```jsonc
"gpu_compat":      ["cuda", "cpu"],   // list[str] subset of {cuda,rocm,mps,xpu,cpu}; tts already has it (:1217); ADD to asr
"effective_device": "cpu",            // str: a DeviceFamily value (or "network" for LLM only)
"routing_status":   "cpu_fallback",   // str enum: accelerated|cpu_fallback|cpu_only|unavailable|n/a
"routing_reason":   "string | null"   // str (scrubbed) or null
```
Computed once per call from a **single** `detect_host_caps()` call (not per-entry — call it once at the top of the loop), then `resolve_routing(cls.gpu_compat, caps)` per entry; spread the returned `RoutingResult` into the dict. **Redaction (corrected — see §Constraints, local-first):** existing `reason`/`last_error` masking at `:1213/1215` uses `_mask_hf_tokens` (HF-only). The new `routing_reason` is serialized through **`core.scrub.scrub_text`** instead — and because `scrub_text(None) → ""` (`scrub.py:77-78`), the wiring must preserve a literal JSON `null` for the "no reason" case: emit `scrub_text(reason) if reason else None` (do **not** pass `None` through `scrub_text`, which would turn it into `""`). Rationale: `routing_reason` can interpolate a `device_name` or note that may contain a home path, and in the availability-raised branch carries an exception message that could contain any of the broader credential shapes `scrub_text` covers (GitHub PAT/classic, OpenAI `sk-`, secret-NAMED env values). `_mask_hf_tokens` alone would miss those. (Optionally upgrade the existing `reason`/`last_error` masking to `scrub_text` too for consistency, but that is out of scope unless free.)

**`asr_backend.list_backends()` is extended to full TTS parity** — it gains `install_hint` (`str | None`), `last_error` (`str | None`), `isolation_mode` (`"in-process" | "subprocess"`), the four routing keys above, **and** applies `scrub_text` to its `reason` (ASR currently emits `reason` unmasked at `:999`, a pre-existing token-leak gap this task closes). After the change, an ASR entry has the **identical 11-key shape** as a TTS entry:
```jsonc
{
  "id": "whisperx", "display_name": "...", "available": true,
  "reason": null, "install_hint": null, "last_error": null,
  "isolation_mode": "in-process",
  "gpu_compat": ["cuda","rocm","cpu"],
  "effective_device": "cuda", "routing_status": "accelerated", "routing_reason": null
}
```
`llm_backend.list_backends()` (`:169`) gains the four routing keys with the `n/a` constants from §3a (and may keep its thinner shape otherwise — the matrix tolerates missing parity fields; ASR is brought to full parity, LLM is not required to be).

**Empty/error states for `list_backends()` enumeration (COMPLETENESS) — exact emitted shapes:**
- An engine whose `is_available()` **raises** must not break the whole list. Each backend's availability and routing must be computed in a per-entry try/except (TTS already does this at `tts_backend.py:1186-1195`; mirror it in ASR) that, on failure, emits exactly: `{"available": false, "reason": "<scrubbed `f"{type(exc).__name__}: {exc}"`>", "routing_status": "unavailable", "routing_reason": "availability check raised", "effective_device": "cpu", "gpu_compat": [...], "install_hint": <hint or null>, "last_error": "<scrubbed>", "isolation_mode": "..."}`. The error text passes through `scrub_text` so a torch/driver exception that interpolated a path or secret comes out clean.
- `gpu_compat` missing on a subclass (forgot the attribute) → `getattr(cls, "gpu_compat", ("cpu",))` keeps the existing ABC-default fallback at `:1217` — never a `KeyError`.
- `detect_host_caps()` returning `probe_ok=False` → routing for every entry is computed against the CPU-only degraded caps (§1a), so the whole list is internally consistent (everything is `cpu_only` or `unavailable`), and no entry claims acceleration the host can't deliver.

### 6. Preflight + diagnose
- `/setup/preflight` (`wizard.py:203`, the `preflight()` body building `checks` from `:205`, returning the dict at `:391-406`): add a `gpu_routing` field to the response computing the **active TTS engine's** routing (`tts_backend.active_backend_id()` + its `gpu_compat`) against `detect_host_caps()`. New check row appended near the existing `gpu` row (`:371-374`): a `PreflightCheck` `{id:"engine_routing", label, status: pass|warn|fail, detail, fix}`. Verdict table (all states enumerated):

  | Active-engine routing | `engine_routing` check status | Detail / fix |
  |---|---|---|
  | `accelerated` (no caveat) | `pass` | `"<engine> will run on <family>"`, fix `None`. |
  | `accelerated` (driver/arch caveat note from §2) | `warn` | detail surfaces the note; fix points at driver/torch-build remedy (reuse wizard's existing fix strings at `:341-348`). |
  | `cpu_fallback` | `warn` | `"<engine> declares <targets> but this <family> host has no matching path — running on CPU (~10× slower)"`; fix suggests switching to an engine that supports `<family>`. |
  | `cpu_only` | `pass` | `"<engine> runs on CPU on this no-GPU host"`, fix `None` (matches local-first guarantee — a no-GPU machine is a supported configuration). |
  | `unavailable` | `fail` | `"<engine> needs <targets>; this host cannot provide it (<family>)"`; fix suggests a compatible engine or installing the right wheels. |
  | active engine `is_available()==false` (deps missing) | `fail` | reuse the engine's `reason`/`install_hint`; the routing field still reports its theoretical status but the check leads with the availability failure. |
  | **no active engine resolvable** (`active_backend_id()` returns an id not in the registry, e.g. an uninstalled-then-removed engine left in prefs) | `warn` | `"No usable active TTS engine selected"`; fix `"Pick an engine in Settings > Engines."` The `gpu_routing` block then reports `{active_engine:"<id>", effective_device:"cpu", routing_status:"unavailable", routing_reason:"active engine not in registry"}`. **Must not raise.** |
  | `detect_host_caps()` `probe_ok==false` | `warn` (not fail) | `"GPU probe unavailable (torch not importable) — routing cannot be verified; app will run CPU-only"`; fix points at backend torch install. Never blocks the wizard. |

  `_detect_gpu()` (`:91-181`) is rebased on `detect_host_caps()` to kill the duplicate nvidia-smi/rocm-smi/hip detection (it may keep the shell-out for `device_name`/`driver` strings, but the family/availability decision delegates to the probe). The `device` block (`:395-405`) gains `gpu_family` and `vram_gb` (see schema note in §API). **The existing `gpu` check row's verdict logic (`:325-374`) is preserved** (apple/nvidia-ready/nvidia-broken/amd/docker-fallback/no-gpu branches) — `engine_routing` is an *additional* row, not a replacement, so the wizard's existing device messaging is untouched. **All new `detail`/`fix`/`label` strings on the backend are English keys/templates only; the user-facing rendering of these check rows in the wizard UI goes through i18n in the frontend (§8, §Constraints localization).**
- `/system/diagnose` (`core/diagnose.py`): `_check_device()` (`:63-87`) and `_check_engines()` (`:172-194`) gain routing awareness.
  - `_check_device()`: keep the existing global device WARN at `:80-85` for the genuine no-GPU case, but when `detect_host_caps()` carries a driver/arch note (CUDA present-but-degraded), upgrade the message from the bare `"cpu"` text to name the specific cause (driver below min / arch not in build) using the probe note.
  - `_check_engines()`: already reads the active backend's row from `list_backends()` (`active_row` at `:181`) and inspects `available`/`reason`/`install_hint` — extend it to read `routing_status`/`routing_reason` from that same row. State table: active engine `available==false` → existing FAIL path unchanged (`:182-189`); `routing_status=="unavailable"` (but available) → FAIL with reason + install hint (e.g. "this engine needs CUDA; your CUDA is unusable: driver 520 < 525.60.13"); `routing_status=="cpu_fallback"` → WARN with the routing reason; `cpu_only`/`accelerated`/`n/a` → existing OK path. **Ordering:** availability FAIL takes precedence over routing (an unavailable engine's routing is moot).

### 7. Synth-time + select-time gating (no silent fallback) — ALL entry points

**Select** (`engines.py:270` `select_engine`, request schema `SelectEngineRequest` at `:264`: `{family: str, backend_id: str}`): the handler already builds `available = {b["id"]: b for b in module.list_backends()}` (`:278`) and guards `available`/`reason` (`:281-283`). After that guard, read `available[req.backend_id].get("routing_status", "cpu_only")`:
  - `"unavailable"` → `raise HTTPException(400, f"Backend {req.backend_id} cannot run on this host: {routing_reason}")` (the engine cannot run on this host at all — mirrors the existing `:283` `not ready` 400 phrasing).
  - `"cpu_fallback"` → still **allow** the select (don't block — the user may knowingly want it), but include the routing fields in the response dict so the UI can show a confirm/warning toast.
  - `"cpu_only"` / `"accelerated"` / `"n/a"` → allow silently (response carries the fields for UI completeness).
  - LLM family (`family=="llm"`, `routing_status=="n/a"`) → skip the routing gate entirely.
  - Edge: `routing_status` key **missing** from the row (older/degraded path) → treat as if `cpu_only` (don't block) — defensive `.get("routing_status", "cpu_only")`.

  **`select_engine` response shape (pinned — extends the current `{family, active, env_override}` at `engines.py:285-289`):**
  ```jsonc
  {
    "family": "tts",
    "active": "cosyvoice",
    "env_override": false,
    "routing_status": "cpu_fallback",     // NEW: from available[backend_id]; "n/a" for llm
    "effective_device": "cpu",            // NEW
    "routing_reason": "engine has no MPS path; running on CPU"  // NEW, scrubbed, may be null
  }
  ```
  The three new keys are **always present** in the success response (additive, non-optional in the response model) so the UI doesn't branch on presence; on a defensive-`.get` path they carry `"cpu_only"`/`"cpu"`/`null`. `SelectEngineResponse` is a new explicit Pydantic model (the endpoint currently returns a bare dict — adding the model both documents the shape and keeps the three new keys from being silently dropped); see §API.

A backend's synth gating is now applied at **all three** synth-producing entry points (each resolves a backend class/instance and currently gates only on `is_available()`). The gating logic is **identical across the three OS targets** — only the resolved `HostCaps` differs by hardware (§Constraints, cross-platform parity):

1. **REST form-POST** (`generation.py:286` `generate_speech`): the non-VoiceStudio branch resolves `backend_cls` and gates on `is_available()` (`:341-349`, 400 at `:346`), then instantiates via `_get_engine_instance` (`:352-353`); the VoiceStudio branch is `:335-339` (`get_model()` — its device is whatever `get_best_device()` returns). After resolving the engine, compute `routing = resolve_routing(backend_cls.gpu_compat, detect_host_caps())`:
   - `"unavailable"` → `raise HTTPException(400, routing["routing_reason"])` (mirrors the existing `is_available()` 400 at `:346`).
   - `"cpu_fallback"` → proceed, and attach routing headers to the `StreamingResponse`. The response already sets 6 custom headers (`:496-503`: `X-Audio-Id`, `X-Gen-Time`, `X-Audio-Path`, `X-Seed`, `X-Audio-Duration`, `Content-Length`); add **`X-VoiceStudio-Routing`** (the `routing_status` string, e.g. `"cpu_fallback"`) and **`X-VoiceStudio-Routing-Reason`** (the scrubbed + ASCII-sanitized + ≤256-char reason). (Note: the body is a WAV byte stream, not JSON — the header channel is the correct carrier.)
   - `"accelerated"` with a non-`None` `routing_reason` (driver/arch caveat) → still 200, set `X-VoiceStudio-Routing: accelerated` and `X-VoiceStudio-Routing-Reason: <caveat>` so the UI can surface "running on GPU, but driver below recommended."
   - `"accelerated"` (no caveat) / `"cpu_only"` → **omit both headers** (benign — nothing to surface).
   - **VoiceStudio native branch (`:335-339`):** must be gated too — VoiceStudio's `gpu_compat` is `("cuda","mps","cpu")`, so on a no-GPU host it's `cpu_only` (no header) and never `unavailable`; but on a host where its declared accelerators are absent it could be `cpu_fallback`. Compute routing for `VoiceStudioBackend.gpu_compat` and emit the same headers. (VoiceStudio can't be `unavailable` since it always lists cpu, so it never 400s on routing — good.)
   - **Header value safety (pinned encoding rule):** HTTP headers are latin-1; `X-VoiceStudio-Routing-Reason` must be (1) `scrub_text`-cleaned, (2) ASCII-sanitized via `reason.encode("ascii", "ignore").decode("ascii")` (or `str.translate`), (3) truncated to ≤256 chars. This is **not a regex** (no CodeQL ReDoS surface — §Constraints). `X-VoiceStudio-Routing` is always one of the lowercase status literals (already ASCII), no sanitizing needed.
   - **Chunked-generation path** (`max_chunk_chars`/`crossfade_ms` long-text splitting, `:308-310`): routing is computed once for the request, not per chunk — the header reflects the single engine used for all chunks.

2. **OpenAI-compat `POST /v1/audio/speech`** (`openai_compat.py:252` `create_speech`, engine resolved by `_resolve_engine` at `:142-170`): `_resolve_engine` already 400s on `is_available()==false` (`:153-158`) and maps `tts-1`/`tts-1-hd` to the active engine (`:147-148`). Extend `_resolve_engine` (or the caller) to compute routing against `detect_host_caps()` after the availability check:
   - `"unavailable"` → `HTTPException(400, routing["routing_reason"])` (consistent with the existing availability 400).
   - `"cpu_fallback"` / `"accelerated"`-with-caveat → set the same `X-VoiceStudio-Routing` / `X-VoiceStudio-Routing-Reason` headers (scrubbed + ASCII-sanitized + ≤256) on whatever response object this handler returns (`Response`/`StreamingResponse` per OpenAI shape) via `response.headers[...] = ...`. If headers cannot be attached for a given output format, the gate still **logs** a structured WARN (so it's never fully silent) — but for the common case attach the headers.
   - The `tts-1`/`tts-1-hd` alias path resolves to the active engine, so its routing is the active engine's routing — identical handling.

3. **WebSocket `/ws/tts`** (`tts_stream.py:87-99`): resolves the engine from `data.get("engine")` or `get_active_tts_backend()`, then streams chunks. It already emits `send_json` frames discriminated by a `"type"` key — `{"type":"start", sample_rate, channels, format, engine}` (`:191-197`) and `{"type":"done", duration_s, gen_time_s, samples, sample_rate, engine}` (`:220-227`). Add a **`routing` frame** in the same convention. Headers aren't available on a WebSocket; instead:
   - After resolving the backend, compute routing. `"unavailable"` → send `{"type":"error","message":"<scrubbed routing_reason>"}` (the handler's existing error-frame convention) and **close** the socket before streaming any audio (don't silently fall back).
   - `"cpu_fallback"` / `"accelerated"`-with-caveat → emit a one-time **routing frame** *before* the first audio chunk / before the `start` frame:
     ```jsonc
     { "type": "routing", "status": "cpu_fallback", "reason": "engine has no MPS path; running on CPU" }
     ```
     where `status` is the `routing_status` literal and `reason` is `scrub_text`-cleaned (JSON frame → no latin-1/ASCII constraint, so no truncation needed, but still scrubbed for token safety; `reason` is the raw scrubbed string or omitted/`null` if `None`). Streaming then proceeds normally.
   - `"cpu_only"` / `"accelerated"` (no caveat) / `"n/a"` → no `routing` frame, stream normally.

**Dub path** (`dub_core.py`, `transcribe` preflight channel at `:398-441`, ASR backend resolved via `get_active_asr_backend` at `:424-430`): append the **active ASR backend's** routing note to the existing `preflight_error` channel (`:415,434`):
  - ASR `cpu_fallback` → a **warning prefix** on the `preflight_error` channel (not a hard error) — transcription on CPU is slow but works; the stream proceeds.
  - ASR `unavailable` on this host → emit it through the same `preflight_error` SSE `error` event (`:441`) as a **blocking** failure carrying the routing reason (scrubbed); the stream dies cleanly (reuse the existing channel that already SSE-emits `preflight_error` as an `error` event).
  - Edge: the ASR backend chosen for dubbing may differ from the standalone ASR pick; route the **resolved** `_asr_backend` (`:430`), not the registry default.

### 8. Frontend matrix — "your machine" routing
`frontend/src/components/EngineCompatibilityMatrix.jsx`:
- Extend `normalizeEntry` (`:80-93`) to read `effective_device`, `routing_status`, `routing_reason` (with safe defaults, matching the existing `gpu_compat` default at `:89-91`). **Defaults when fields are absent (legacy/degraded backend):** `routing_status` → `undefined`/`null` (render no badge, just the declared chips, exactly as today); `effective_device` → `null` (no chip highlight). This preserves the current rendering for any payload that predates this task (additive, backward-compatible — §Constraints).
- GPU compat cell (`:311-319`, chips `.map` at `:313-317`, using the `GPU_LABEL` map at `:70-75`): keep the declared chips, but **highlight** the chip matching `effective_device`, and add a small effective-device badge with tone by status (`accelerated`→`success`, `cpu_fallback`→`warn`, `cpu_only`→`neutral`, `unavailable`→`danger`, `n/a`→`neutral`) + `title={routing_reason}`. Reuse the existing `<Badge tone=… size="xs">` pattern already used for availability at `:306-307`. Extend `GPU_LABEL` if a new key (e.g. `network`) is needed.
- **Empty / unknown / conflicting states (COMPLETENESS):**
  - **`available == false`:** the availability state dominates — render the existing "not installed / unavailable" treatment and **dim or omit** the routing badge (don't show a confusing "cpu_fallback" badge for an engine that isn't installed; an uninstalled engine's routing is hypothetical). This resolves the MLX-on-Linux case (§3): `available:false` → no live routing badge.
  - **Unknown `routing_status`** (a future status the UI doesn't recognize) → fall back to a `neutral` tone and show the raw reason in `title`, never crash the map. (Switch with a `default` arm.)
  - **`routing_reason` null but status is `cpu_fallback`/`unavailable`** (shouldn't happen per §2, but defend) → render the badge with a generic i18n string for that status, no `title`.
  - **`effective_device` not in `GPU_LABEL`** (e.g. `network` for LLM, or a future family) → render the raw value or a neutral label rather than `undefined`.
  - **LLM rows** → neutral "remote / off" badge (the `n/a` status), no device-chip highlight.
- Optional header chip showing the detected host device once (from any family entry — they share the same host caps). Edge: if **all** entries lack routing (legacy payload), suppress the header chip entirely.
- **New i18n keys under `engines.*`** (the namespace exists in `frontend/src/i18n/locales/en.json` at the `"engines"` object on line `1222`; existing keys include `available`/`unavailable`/`installedAndReady`/`notInstalled`). Add e.g. `engines.runsOn`, `engines.cpuFallback`, `engines.routingUnavailable`, `engines.cpuOnly`, `engines.routingNa`, and a generic `engines.routingUnknown` (for the unknown-status fallback) to **all 21 locale files** in `frontend/src/i18n/locales/*.json` (English source `en.json` + the 20 translated locales: ar, de, es, fr, hi, id, it, ja, ko, nl, pl, pt, ru, sv, th, tr, uk, vi, zh-CN, zh-TW — verified present in the working tree) per the localization hard rule. **No literal user-facing string (including the CJK locales) is hardcoded in JSX — every routing label/badge/toast text resolves through `t('engines.…')`.** GPU family abbreviations themselves (CUDA / MPS / ROCm / CPU / network) stay in the JS `GPU_LABEL` map (functional identifiers, not prose — exempt from i18n, consistent with the existing map at `:70-75`). The `zh-CN.json`/`zh-TW.json` additions are *translations of English keys*, never new hardcoded CJK in code, so `tests/test_no_hardcoded_cjk.py` stays green (the locale files are inside the translation layer it explicitly allows).
- `frontend/src/api/types.ts`: extend `EngineBackend` (`:21-32`, the optional-fields block documented by the `:14-20` comment) with the three optional routing fields, and extend `SelectEngineResponse` with the matching fields returned by §7:
  ```ts
  // EngineBackend (additive, all optional for backward-compat with legacy payloads)
  effective_device?: GPUTarget | 'network';
  routing_status?: 'accelerated' | 'cpu_fallback' | 'cpu_only' | 'unavailable' | 'n/a';
  routing_reason?: string | null;
  // SelectEngineResponse (:45-49) — the three keys are present on every success response (§7)
  routing_status?: 'accelerated' | 'cpu_fallback' | 'cpu_only' | 'unavailable' | 'n/a';
  effective_device?: GPUTarget | 'network';
  routing_reason?: string | null;
  ```
  (`GPUTarget` is already defined at `:20`.)
- **Synth-time UI handling of `X-VoiceStudio-Routing`:** the REST/OpenAI fetch wrapper that calls `/api/generate` (and `/v1/audio/speech`) reads the response headers; when `X-VoiceStudio-Routing` is `cpu_fallback` (or `accelerated` with a non-empty `X-VoiceStudio-Routing-Reason`), show a **one-time, non-blocking** toast with the reason (i18n key). The WS client handles the `routing` frame (`type === "routing"`) analogously, reading `status` + `reason`. Edge: header absent / no frame → no toast (legacy/benign). De-duplicate so a batch run doesn't spam one toast per request (track last-shown status per engine, in-memory). **No client-side persistence of the dedupe state in `localStorage` is introduced** — it's in-memory per session; there is therefore **no localStorage schema and no lazy-migration concern** for this task (§Constraints, backward-compatible data).

## Integration points (file:line — verified)

- `backend/services/tts_backend.py:103` — `gpu_compat` ABC default (anchor).
- `backend/services/tts_backend.py:172,257,366,460,553,682,846,955` — per-engine tuples (add `rocm` where valid per §4).
- `backend/services/tts_backend.py:579` — `MLXAudioBackend.is_available()` (#390 gate; add `mlx_supported()` call before `import mlx_audio`).
- `backend/services/tts_backend.py:41-53` — `_HF_TOKEN_MASK_RE` (bounded `hf_[A-Za-z0-9]{30,}`, no ReDoS) + `_mask_hf_tokens` (HF-only — see §5: routing reasons use the broader `core.scrub.scrub_text` instead).
- `backend/core/scrub.py:70` — `scrub_text(text: str | None) -> str` (HF + GitHub PAT/classic + OpenAI `sk-` + home dirs + secret-NAMED env values; **never raises; `None`→`""`** so the §5 wiring guards with `scrub_text(r) if r else None`). **Reuse for all `routing_reason` serialization.**
- `backend/services/tts_backend.py:1151-1219` — `list_backends()` (add 4 routing keys, single `detect_host_caps()` call, per-entry try/except resilience at `:1186-1195`; routing reason scrubbed via `scrub_text(r) if r else None`; existing `reason`/`last_error` masked at `:1213/1215`).
- `backend/engines/indextts/__init__.py:41,78` — `IndexTTS2Backend` (add explicit `gpu_compat`, drops the CPU-only default).
- `backend/engines/omnivoice_gguf/backend.py:296` — gguf `gpu_compat` (rocm audit).
- `backend/engines/supertonic3/backend.py:75` — Supertonic-3 `gpu_compat = ("cpu",)` (leave unless ONNX GPU EP added).
- `backend/services/asr_backend.py:37` — `ASRBackend` ABC (add `gpu_compat: tuple[str,...] = ("cpu",)`).
- `backend/services/asr_backend.py:61,382,497,566,632,741,886` — per-engine ASR tuples.
- `backend/services/asr_backend.py:507,510` — `MLXWhisperBackend.is_available()` (replace inline MPS check with shared `mlx_supported()`).
- `backend/services/asr_backend.py:991-1001` — ASR `list_backends()` (full parity: add gpu_compat + 4 routing keys + install_hint/last_error/isolation_mode + `scrub_text` on `reason` + per-entry try/except mirroring TTS).
- `backend/services/llm_backend.py:37,163-166,169-179` — `LLMBackend` ABC (`gpu_compat = ()`) + registry + `list_backends()` (`n/a` routing constants, §3a).
- `backend/services/model_manager.py:195-233` — `get_best_device()` (delegate family to `device_caps`; preserve `_configure_rocm_if_needed`/DirectML/XPU side-effects).
- `backend/services/model_manager.py:139-162` — `_configure_rocm_if_needed` + `_ROCM_GFX_OVERRIDES` (`:130-135`) (referenced by probe notes; **not** called by the probe).
- `backend/services/model_manager.py:167-192` — `check_device_compatibility()` (SM-arch check; surfaced as a note; list-membership logic reused — no regex).
- `backend/services/gpu_sandbox.py:150` — `is_sandbox_available()` (reconcile / leave; not a routing source).
- `backend/engines/omnivoice_gguf/hardware_probe.py:54-124` — rebase `detect_capabilities()` family/vram on `device_caps` (ROCm now distinct; keep `_bucket()` API + the `except → 0` VRAM guard at `:100-105`; MB↔GB unit conversion explicit).
- `backend/api/routers/engines.py:38-68` — `/engines`, `/engines/{family}` (carry routing automatically via `list_backends()`).
- `backend/api/routers/engines.py:176,237` — `_get_engine_instance` cache (instance reuse; routing computed independently of cache).
- `backend/api/routers/engines.py:264-289` — `SelectEngineRequest` + `select_engine` (gate `unavailable` with pinned 400 detail, return the 3 routing keys, skip for LLM, defensive `.get("routing_status","cpu_only")`); add a `SelectEngineResponse` model (§API).
- `backend/api/routers/generation.py:286,308-310,335-339,341-353,496-503` — REST synth handler, VoiceStudio + non-VoiceStudio branches, chunking params, `X-` header dict (add `X-VoiceStudio-Routing` + `-Reason`, scrubbed + ASCII-sanitized + ≤256; omitted for benign statuses).
- `backend/api/routers/openai_compat.py:139-170,252-300` — `_resolve_engine` + `create_speech` (routing gate; `tts-1`/`tts-1-hd` alias resolves to active engine; attach routing headers / log WARN).
- `backend/api/routers/tts_stream.py:87-99,191-197,220-227` — `/ws/tts` engine resolution + `send_json` `start`/`done` frames (add `{"type":"routing",...}` frame before first audio / `{"type":"error",...}` + close for `unavailable`; reason scrubbed).
- `backend/api/routers/setup/wizard.py:74,91-181,202-406` — `_MIN_NVIDIA_DRIVER`, `_detect_gpu` rebase, `preflight()` + `gpu_routing` + `engine_routing` check (full state table §6) + `device` block extension (`:395-405`); preserve existing `gpu` row branches (`:325-374`).
- `backend/core/diagnose.py:63-87,172-194` — `_check_device` / `_check_engines` routing-aware (availability FAIL precedes routing; degraded-CUDA note surfacing).
- `backend/api/routers/dub_core.py:398-441` — ASR routing note appended to `preflight_error` channel (ASR resolved at `:424-430`; `cpu_fallback` warns, `unavailable` blocks via the `error` SSE event at `:441`; reason scrubbed).
- `backend/api/schemas.py:108-139` — `PreflightCheck` (`:108`, `extra="allow"`), `DeviceInfo` (`:119`, `extra="allow"`), `PreflightResponse` (`:134`, **no** `extra="allow"`) — see §API schema note + new `GpuRouting` model.
- `frontend/src/api/types.ts:20-32,45-49` — `GPUTarget`, `EngineBackend`, `SelectEngineResponse` routing fields.
- `frontend/src/components/EngineCompatibilityMatrix.jsx:70-75,80-93,306-307,311-319` — `GPU_LABEL`, `normalizeEntry`, Badge pattern, GPU cell, empty/unknown-state arms.
- frontend fetch/WS client(s) calling `/api/generate`, `/v1/audio/speech`, `/ws/tts` — read `X-VoiceStudio-Routing*` headers / `routing` frame, show de-duplicated one-time toast (in-memory dedupe, no localStorage).
- `frontend/src/i18n/locales/*.json` — new `engines.*` routing keys (all 21 locales; `en.json` `"engines"` object at `:1222`).

## API / data shapes

This is the **canonical, single-source reference** for every wire shape this task touches. A developer can implement against these without reading the prose above.

### Function signatures (backend, new)

```python
# backend/core/device_caps.py
DeviceFamily = Literal["cuda", "rocm", "mps", "xpu", "cpu"]

@dataclass(frozen=True)
class HostCaps:
    family: DeviceFamily
    available_families: tuple[DeviceFamily, ...]   # always includes "cpu"
    device_name: str = ""
    vram_gb: float = 0.0
    driver: str | None = None
    notes: tuple[str, ...] = ()
    probe_ok: bool = True

def detect_host_caps() -> HostCaps: ...   # cached per-process; never raises; no network
def refresh() -> HostCaps: ...            # test-only cache clear+reprobe
def mlx_supported() -> tuple[bool, str]:  # (ok, reason); ok True only on Apple Silicon + MPS

# backend/services/engine_routing.py
RoutingStatus = Literal["accelerated", "cpu_fallback", "cpu_only", "unavailable", "n/a"]

class RoutingResult(TypedDict):
    effective_device: str          # DeviceFamily value or "cpu"
    routing_status: RoutingStatus  # resolver never returns "n/a"
    routing_reason: str | None

def resolve_routing(gpu_compat: tuple[str, ...], caps: HostCaps) -> RoutingResult: ...
```

### `/engines` and `/engines/{family}` — per-backend entry

TTS and ASR entries (identical 11-key shape after this task):
```jsonc
{
  "id": "cosyvoice",
  "display_name": "CosyVoice 3 (...)",
  "available": true,
  "reason": null,                          // str (scrubbed) | null
  "install_hint": "...",                   // str | null
  "last_error": null,                      // str (scrubbed) | null
  "isolation_mode": "in-process",          // "in-process" | "subprocess"
  "gpu_compat": ["cuda", "cpu"],           // list[str] ⊂ {cuda,rocm,mps,xpu,cpu}
  "effective_device": "cpu",               // NEW — DeviceFamily value
  "routing_status": "cpu_fallback",        // NEW — accelerated|cpu_fallback|cpu_only|unavailable
  "routing_reason": "engine has no MPS path; running on CPU on this Apple Silicon host."  // NEW, scrubbed | null
}
```
LLM entry (§3a) — the four routing keys carry literal constants, `effective_device` may be the only non-GPU value:
```jsonc
{ "id": "openai-compat", "display_name": "...", "available": false, "reason": "...",
  "effective_device": "network", "routing_status": "n/a", "routing_reason": null }
```
Degraded probe (`probe_ok==false`, §1a): every entry resolves against CPU-only caps → `effective_device:"cpu"`, `routing_status:"cpu_only"` (cpu-listing engines) or `"unavailable"` (no-cpu engines), `routing_reason` naming the probe failure.
Availability-check raised (§5): `{ "available": false, "reason": "<scrubbed `ExcType: msg`>", "routing_status": "unavailable", "routing_reason": "availability check raised", "effective_device": "cpu", ... }`.

### `POST /engines/select` — request + response

Request (`SelectEngineRequest`, unchanged): `{ "family": "tts" | "asr" | "llm", "backend_id": "cosyvoice" }`.

Response (`SelectEngineResponse`, NEW model — extends current bare `{family, active, env_override}`):
```jsonc
{
  "family": "tts",
  "active": "cosyvoice",
  "env_override": false,
  "routing_status": "cpu_fallback",   // NEW — always present; "n/a" for llm; "cpu_only" on defensive path
  "effective_device": "cpu",          // NEW
  "routing_reason": "engine has no MPS path; running on CPU"  // NEW, scrubbed | null
}
```
Error: engine `unavailable` on this host → `HTTPException(400, "Backend {id} cannot run on this host: {routing_reason}")`. Unknown family / unknown backend / deps-missing 400s are unchanged (`engines.py:276,280,283`).

### `GET /setup/preflight` — additions

```jsonc
{
  "ok": true,
  "has_warnings": true,
  "checks": [
    /* ...existing rows... */
    { "id": "engine_routing", "label": "Active engine acceleration",
      "status": "warn", "detail": "...", "fix": "..." }   // NEW PreflightCheck row
  ],
  "device": {
    "os": "linux", "arch": "x86_64",
    "gpu_vendor": "amd", "gpu_backend": "rocm",
    "gpu_available": true, "gpu_driver": "...", "gpu_device_name": "...",
    "ram_gb": 64.0, "disk_free_gb": 412.0,
    "gpu_family": "rocm",     // NEW explicit DeviceInfo field (default "cpu")
    "vram_gb": 16.0           // NEW explicit DeviceInfo field (default 0.0)
  },
  "gpu_routing": {            // NEW — GpuRouting model (explicit field; see schema trap below)
    "active_engine": "cosyvoice",
    "effective_device": "cpu",
    "routing_status": "cpu_fallback",
    "routing_reason": "..."   // scrubbed | null
  }
}
```
`gpu_routing` degenerate states: active engine not in registry → `{ "active_engine":"<id>", "effective_device":"cpu", "routing_status":"unavailable", "routing_reason":"active engine not in registry" }`; probe failed → `{ "active_engine":"<id>", "effective_device":"cpu", "routing_status":"cpu_only", "routing_reason":"GPU probe unavailable (torch not importable)" }`.

**Pydantic schema work (`backend/api/schemas.py`) — pinned:**
```python
# NEW model
class GpuRouting(BaseModel):
    active_engine: str
    effective_device: str = "cpu"
    routing_status: str            # accelerated|cpu_fallback|cpu_only|unavailable|n/a
    routing_reason: str | None = None

# DeviceInfo (:119) — add two EXPLICIT typed fields (it has extra="allow", so they'd pass
# through regardless, but make them explicit for clarity/typing):
class DeviceInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    # ...existing fields...
    gpu_family: str = "cpu"        # NEW
    vram_gb: float = 0.0           # NEW

# PreflightResponse (:134) — does NOT set extra="allow", so gpu_routing MUST be an explicit
# field or it is dropped from the serialized response:
class PreflightResponse(BaseModel):
    ok: bool
    has_warnings: bool = False
    checks: list[PreflightCheck] = Field(default_factory=list)
    device: DeviceInfo
    gpu_routing: GpuRouting | None = None   # NEW — required to avoid the extra="allow" trap
```
The new `engine_routing` check row needs **no** schema change (it's just another `PreflightCheck`, which already has `extra="allow"`).

### Synth responses — headers + WS frame

REST `StreamingResponse` (`generation.py:496-503`) and OpenAI-compat `Response` (`openai_compat.py:252`) — two NEW headers, **added only when `routing_status` is `cpu_fallback`, or `accelerated` with a non-`None` caveat reason** (omitted for `cpu_only` / `accelerated`-no-caveat / `n/a`):
```
X-VoiceStudio-Routing: cpu_fallback          # the routing_status literal (lowercase, ASCII)
X-VoiceStudio-Routing-Reason: <scrubbed, ASCII-sanitized via .encode("ascii","ignore"), ≤256 chars>
```
WS `/ws/tts` (`tts_stream.py`) — NEW `routing` frame, emitted once before the first audio chunk for `cpu_fallback` / `accelerated`-with-caveat (sibling to the existing `start`/`done` frames):
```jsonc
{ "type": "routing", "status": "cpu_fallback", "reason": "<scrubbed>" }   // reason str | omitted if null
```
WS `unavailable` → `{ "type": "error", "message": "<scrubbed routing_reason>" }` then socket close before any audio.

### Dub SSE (`dub_core.py:398-441`) — `preflight_error` channel

- ASR `cpu_fallback`: existing `preflight_error` event gains a **warning prefix** (scrubbed routing reason); stream proceeds.
- ASR `unavailable`: emitted as the existing **`error`** SSE event (`:441`) with the scrubbed routing reason; stream dies cleanly.

### DB schema + migration

**N/A — none.** This task adds no DB model, no alembic revision, and touches no `core/db.py`/`core/job_store.py` schema. The only persisted state (the engine pick) reuses the existing `prefs.json` key path via `prefs.set_(pref_key, ...)` (`engines.py:284`). No localStorage schema is introduced either (synth-toast dedupe is in-memory per session). Recorded here so the migration dimension of this lens is explicitly closed, not overlooked (§Constraints, backward-compatible data).

### `HostCaps` examples (per host class)

```
NVIDIA ready:   HostCaps(family="cuda", available_families=("cuda","cpu"), device_name="NVIDIA RTX 4090", vram_gb=24.0, driver="560.35", notes=(), probe_ok=True)
NVIDIA old drv: HostCaps(family="cuda", available_families=("cuda","cpu"), device_name="NVIDIA RTX 3090", vram_gb=24.0, driver="520.61", notes=("driver 520 < 525.60.13 required — may fail at kernel launch",), probe_ok=True)
AMD ROCm:       HostCaps(family="rocm", available_families=("rocm","cpu"), device_name="AMD Radeon RX 7900", vram_gb=20.0, driver="6.2.41134", notes=(), probe_ok=True)
Apple Silicon:  HostCaps(family="mps",  available_families=("mps","cpu"),  device_name="Apple Silicon (MPS)", vram_gb=24.0, driver=None, notes=(), probe_ok=True)
Windows DML:    HostCaps(family="cpu",  available_families=("cpu",),       device_name="", vram_gb=0.0, driver=None, notes=("DirectML device present (Windows GPU); torch-family probe treats as non-accelerated",), probe_ok=True)
No GPU:         HostCaps(family="cpu",  available_families=("cpu",),       device_name="", vram_gb=0.0, driver=None, notes=(), probe_ok=True)
torch broken:   HostCaps(family="cpu",  available_families=("cpu",),       device_name="", vram_gb=0.0, driver=None, notes=("torch not importable; treating host as CPU-only",), probe_ok=False)
```

## Constraints — VoiceStudio hard rules

Each relevant CLAUDE.md hard rule and exactly how this task satisfies it. (This section is normative — a slice that violates one of these is a P0, not a nit.)

- **Cross-platform parity / "defaults must work on every platform" (strict, 2026-05-20).**
  - The default-everywhere feature this task ships is the **routing model + no-silent-fallback signalling** — the probe, resolver, `/engines` payload shape, preflight `gpu_routing`/`engine_routing` row, synth-time gating, and the matrix UI. **Code path, payload shape, and UI rendering are byte-identical on macOS / Windows / Linux.** Only the *computed values* differ, because they reflect the user's actual hardware — which is the entire point of the feature, not a divergence.
  - Platform-specific *implementation code* is confined to OS-API detection inside `device_caps.py` (CUDA/ROCm via `torch.cuda`, MPS via `torch.backends.mps`, XPU via IPEX, DirectML via `torch_directml`, nvidia-smi/rocm-smi shell-outs for name strings). These are allowed (OS APIs), and none change the user-visible *default behavior* contract.
  - **MLX is hardware-gated, not platform-divergent default behavior.** MLX engines are intrinsically Apple-Silicon-only; `mlx_supported()` fences them behind a hardware capability exactly as a macOS-only feature would sit behind an opt-in. On non-Apple hosts they report `available:false` (not a broken default) — the no-silent-fallback machinery still runs identically and correctly classifies them.
  - **No platform gets a worse outcome on the same hardware class.** The DirectML (Windows) and XPU edges are explicitly handled (§1a/§2) so those users get a neutral `cpu_only`/`cpu_fallback` with an explanatory reason, never a spurious `unavailable`/400. There is no "fix it on the missing platform or move behind opt-in" gap — the feature is fixed on all platforms.

- **Local-first guarantee preserved (no cloud / accounts / API keys / telemetry).**
  - `detect_host_caps()` makes **zero network calls** — driver/sysctl/torch-metadata queries only. No data leaves the machine.
  - The only outbound-capable engine, `OpenAICompatBackend`, is unchanged and opt-in by config; `effective_device:"network"` is a *static label*, not a probe or a call (§3a).
  - **Degrade-not-die / fully-functional-with-no-GPU:** a no-GPU host is a first-class supported configuration — `cpu_only` is a `pass` in preflight, never a warn/block (§6). A broken/missing torch degrades to a cached CPU-only `probe_ok=False` result; the app stays fully functional and `/engines`/synth never 500 (§1a, §5).
  - **Token/secret safety (corrected vs. prior draft):** routing reasons can interpolate a `device_name`, a probe note, or — in the availability-raised branch — an exception message. All `routing_reason` strings are therefore serialized through **`core.scrub.scrub_text`** (`backend/core/scrub.py:70`), which redacts HF tokens, GitHub PAT/classic tokens, OpenAI `sk-` keys, home directories, **and** the values of any `*TOKEN*|*KEY*|*SECRET*|*PASSWORD*|*CREDENTIAL*`-named env var — strictly stronger than `_mask_hf_tokens` (HF-only). This also closes a pre-existing gap: ASR `reason` (`asr_backend.py:999`) is currently emitted unmasked and gains scrubbing here. Because `scrub_text(None) → ""`, the wiring guards with `scrub_text(r) if r else None` so a JSON `null` stays `null` (not `""`). Header reasons are additionally ASCII-sanitized + length-capped (§7). `scrub_text` never raises, so scrubbing failure can't break a response.

- **Backward-compatible project data (no manual migration; alembic for DB; lazy migration for localStorage).**
  - **No DB schema change** — this task touches no model under `core/db.py`/`core/job_store.py`; no alembic revision is added (recorded explicitly in §API → DB schema + migration as N/A).
  - **No localStorage schema** is introduced. The synth-toast dedupe state is in-memory per session (§8) — there is nothing to lazily migrate, no version key, no legacy-shape read.
  - **All API additions are additive and optional.** `gpu_compat` default stays `("cpu",)`; `effective_device`/`routing_status`/`routing_reason` are new optional keys on `/engines`, and `GpuRouting`/`gpu_routing` is an optional (`| None = None`) field on `PreflightResponse`. The matrix already tolerates missing fields (`types.ts:14-31`, `EngineCompatibilityMatrix.jsx:89-91`), and §8 specifies the exact legacy-payload rendering (render as today, no badge). A new client against an old backend, or an old client against a new backend, both work.
  - **On-disk model state untouched** — `get_best_device()`'s device-string contract and side-effects (`_configure_rocm_if_needed`, DirectML, XPU) are preserved (§1, Non-goals). Existing IndexTTS/CosyVoice/etc. installs are not reinstalled or migrated. The only changed *declared* value is IndexTTS2's inherited CPU-only default → its real `("cuda","cpu")` tuple — a correction of a latent bug, not a state migration.

- **CodeQL py/polynomial-redos (no super-linear regex reachable from user input).**
  - **No new regex is introduced on any user-input-reachable path.** The probe parses the driver version with `int((driver or "0").split(".")[0])` (string split, no regex). `mlx_supported()` and the resolver use exact string/membership comparisons. The header ASCII-sanitizer uses `.encode("ascii","ignore")`/`str.translate`, not a pattern. The SM-arch check reuses `check_device_compatibility()`'s existing list-membership logic.
  - The **only** regexes in the redaction path are the pre-existing, already-CodeQL-clean ones: `_HF_TOKEN_MASK_RE = hf_[A-Za-z0-9]{30,}` (`tts_backend.py:41`) and `core.scrub`'s bounded token/home patterns (`scrub.py:34-51`) — all single-quantifier, no nested/overlapping quantifiers, no super-linear backtracking, each `.sub` wrapped so a failure can't raise. This task adds none.
  - Routing reasons are author-controlled English literals, but they are still scrubbed (above) — so even on the theoretical path where an interpolated value reaches a regex, the regex is one of the bounded, audited patterns.

- **Localization (no hardcoded non-English / CJK user-facing text; all UI via i18n `t()` keys).**
  - Every new user-facing string — matrix routing badges/titles, the synth toast, the preflight/diagnose verdicts as rendered in the UI — resolves through `t('engines.…')`. No literal prose is hardcoded in JSX.
  - New keys are added to **all 21 locale files** (`en.json` source + 20 translated locales, verified present), with the `zh-CN`/`zh-TW` entries being *translations of English keys inside the translation layer* — never new hardcoded CJK in code. `tests/test_no_hardcoded_cjk.py` stays green (locale files are explicitly within the allowed translation layer; this task adds nothing to `_ALLOWED_FILES`).
  - GPU family abbreviations (CUDA / MPS / ROCm / CPU / network) live in the JS `GPU_LABEL` map as **functional identifiers**, not prose — consistent with the existing map and exempt from i18n.
  - Backend `label`/`detail`/`fix` strings on preflight rows are English templates/keys; their user-facing rendering is the frontend's i18n responsibility.

- **Versioning (continuous-to-main patch; no RC; no defer).**
  - Version files are at `0.3.6` (latest release `v0.3.5` + 1 patch) and stay there; this task bumps **nothing** and invents no RC/codename.
  - All five PR slices land **continuous-to-main** and each is independently green and shippable (§PR slices). No `-rc` tag, no soak, no `v0.4` deferral — scope is absorbed into the open v0.3.x line. #390 is closed within this line, not re-versioned.

- **Docs-sync (same-PR doc updates).**
  - README/docs sections describing GPU/platform support and the engine compatibility matrix are updated **in the same PR** as the behavior change if they assert routing behavior; a CHANGELOG entry lands with PR 4 (the first user-visible behavior slice). Stale docs are treated as bugs (§Test plan / §PR slices).

## Test plan

New/updated pytest (run via `uv run pytest`):
- `tests/backend/test_device_caps.py` (new; `tests/backend/` exists): monkeypatch `torch.cuda.is_available` / `torch.cuda.device_count` / `torch.version.hip` / `torch.backends.mps` / `torch.xpu` / `sys.platform` / `platform.machine` to assert each family is detected, ROCm ≠ CUDA, `available_families` always contains `cpu` (invariant), driver-below-`_MIN_NVIDIA_DRIVER` adds a note, SM-arch-mismatch (via `check_device_compatibility`) adds a note. Assert the **exact `HostCaps` field values** match the §API "HostCaps examples" table per host class. **Plus the failure/empty paths:** torch import raises → `probe_ok=False`, family `cpu`, no exception; `cuda.is_available()` raises → swallowed, CPU; `device_count()==0` with `is_available()==True` → treated as no CUDA; `mem_get_info()` raises → `vram_gb=0.0` + note; multi-GPU (`device_count()>1`) → device-0 note; `gcnArchName` missing → no crash; `refresh()` re-probes after monkeypatch. **Assert no network access (no socket) during a probe** (local-first).
- `tests/backend/test_engine_routing.py` (new): table-driven `resolve_routing` — every (gpu_compat × host family) combo → expected `RoutingResult` (exact `effective_device`/`routing_status`/`routing_reason` shape: accelerated, cpu_fallback, cpu_only, unavailable, ROCm-not-in-set, XPU-no-path, empty-tuple defensive, DirectML-note→cpu_only-not-unavailable, degraded probe→no invented acceleration, driver-caveat→accelerated-with-reason). Assert `routing_status` is **never** `"n/a"` from the resolver. Assert routing is identical for a given `HostCaps` regardless of mocked `sys.platform` (parity: the resolver is host-fact-driven, not OS-string-driven).
- `tests/backend/test_mlx_supported.py` (new or folded in): `mlx_supported()` returns the **exact `(bool, str)` tuples** from §3 on linux/x86_64, mac-Intel, Apple-Silicon-no-MPS, torch-unimportable; `(True, "")` only on darwin+arm64+MPS.
- `tests/backend/test_routing_redaction.py` (new or folded in): a `routing_reason` interpolating a fake `hf_<40 chars>`, a `gh[pousr]_<token>`, an `sk-<key>`, a `/home/<name>/...` path, and a secret-named env value → all redacted by `scrub_text` in the `/engines` payload, the WS frame, and the `X-VoiceStudio-Routing-Reason` header (local-first / token safety acceptance). Also assert a `None` routing reason serializes as JSON `null` (not `""`) — the `scrub_text(r) if r else None` guard.
- `tests/backend/api/test_engines_route_shape.py` (exists): extend the `required` set at `:84-87` with `effective_device`/`routing_status`/`routing_reason`; the loop currently only checks `body["tts"]["backends"]` (`:88`) — add a parallel assertion that **ASR entries now include the full 11-key shape** (`gpu_compat` + `install_hint`/`last_error`/`isolation_mode` + the 3 routing keys), and that LLM entries carry `effective_device:"network"`/`routing_status:"n/a"`/`routing_reason:null`. Keep the existing `test_gpu_compat_omnivoice_has_cuda_mps_cpu` (`:115-120`) green and add: VoiceStudio on a mocked CUDA host → `routing_status:"accelerated"`/`effective_device:"cuda"`, on a mocked Mac → `accelerated`/`mps`, CosyVoice on a mocked Mac → `cpu_fallback`/`cpu`, any engine on degraded probe → `cpu_only`/`unavailable` (never `accelerated`), an engine whose `is_available()` raises → `available:false`+`routing_status:"unavailable"`+`routing_reason:"availability check raised"` (list doesn't 500).
- `tests/test_engines.py` (exists): extend for the new fields / IndexTTS2 no-longer-CPU-only assertion (`gpu_compat == ["cuda","cpu"]` or `+rocm`).
- **#390 regression:** `MLXAudioBackend.is_available()` returns `(False, ...)` and is `unavailable`/`available:false` when `sys.platform`/`platform.machine` mocked to Linux even with `mlx_audio` importable; `MLXWhisperBackend` parity via the shared `mlx_supported()`; matrix renders `available:false` (badge suppressed) not a misleading routing chip.
- `tests/test_setup_preflight.py` (exists): assert the `gpu_routing` block matches the **exact `GpuRouting` shape** + `engine_routing` check present with correct verdict on mocked hosts for **every** §6 state (accelerated/pass, accelerated-caveat/warn, cpu_fallback/warn, cpu_only/pass, unavailable/fail, active-engine-unavailable/fail, active-engine-not-in-registry/warn, probe-failed/warn); assert `device.gpu_family`/`device.vram_gb` present and that **`PreflightResponse` does not drop `gpu_routing`** (the `extra="allow"` trap — round-trip the response model and assert `gpu_routing` survives serialization).
- `tests/test_diagnose.py` (exists): active-engine `cpu_fallback` → `_check_engines` WARN with reason; `unavailable` → FAIL; availability FAIL still precedes routing; `_check_device` surfaces the driver/arch note instead of bare `"cpu"` when degraded-CUDA.
- `tests/test_generate_engine.py` (exists): REST synth with an `unavailable` engine → 400; `cpu_fallback` → 200 with `X-VoiceStudio-Routing: cpu_fallback` + `-Reason` headers; `accelerated`-with-caveat → 200 with caveat reason header; `accelerated`-no-caveat / `cpu_only` → **no** routing headers; VoiceStudio native branch never 400s on routing; header value is ASCII (no latin-1 crash) even with a non-ASCII device name mocked; `-Reason` ≤256 chars; header reason is scrubbed (no token/path leak).
- `tests/` for OpenAI-compat: `POST /v1/audio/speech` with `unavailable` engine → 400; `cpu_fallback` → 200 + routing headers on the returned `Response`; `tts-1` alias resolves to active engine's routing.
- `tests/` for WS `/ws/tts`: `unavailable` → `{"type":"error",...}` frame + close before any audio; `cpu_fallback` → one `{"type":"routing","status":"cpu_fallback","reason":...}` frame before `start`/first chunk; `accelerated`-clean → no routing frame; frame `reason` scrubbed.
- Backend select: `select_engine` on an `unavailable` engine → 400 with the pinned detail string; `cpu_fallback` → 200 with the 3 routing fields in the response (`SelectEngineResponse`); LLM family select skips the gate (`routing_status:"n/a"` returned, never blocks); missing `routing_status` key in the row → not blocked (defensive `.get` → `cpu_only`).
- Dub: active ASR `cpu_fallback` → `preflight_error` carries a warning prefix but stream proceeds; ASR `unavailable` → SSE `error` event with routing reason (scrubbed).
- Frontend (`bunx vitest run`): RTL test for `EngineCompatibilityMatrix` — `cpu_fallback` entry → warn-tone badge + reason title; `accelerated` → matching chip highlighted; LLM rows → neutral `n/a` badge; `available:false` entry → routing badge suppressed; unknown `routing_status` → neutral fallback, no crash; legacy payload with no routing fields → renders exactly as today (no badge). One-time toast on `X-VoiceStudio-Routing` header de-duplicates across a batch (in-memory). **No new hardcoded strings (i18n keys only)** — assert all badge/toast text resolves via `t()`.
- Lint gate: `tests/test_no_hardcoded_cjk.py` (exists) unaffected (English-only new strings via i18n; new CJK lives only in `zh-CN.json`/`zh-TW.json` inside the translation layer).
- **i18n coverage gate:** assert the new `engines.*` keys exist in **all 21** `locales/*.json` (no missing-key gap that would fall back to English on a non-English locale).

## Dependencies

None new. Uses `torch` (lazy), `psutil`, `platform`, `sys` — all already pinned. No `pyproject.toml` change.

## Risk

- **Mislabeling an engine's ROCm support** (declaring `rocm` where it doesn't actually run) would flip a real `cpu_fallback` into a false `accelerated`. Mitigation: only add `rocm` with an upstream-support code comment; when uncertain, omit (correctly yields `cpu_fallback`, the safe direction).
- **`detect_host_caps()` cost on cold start** — must stay kernel-free (driver/sysctl calls only: `torch.cuda.mem_get_info`, `torch.backends.mps.is_available`, `psutil.virtual_memory`, `get_device_capability`, `_get_arch_list`, and the wizard's `nvidia-smi`/`rocm-smi` shell-outs only if kept for name strings), mirroring `hardware_probe`'s contract (`:20-24`); cached per-process; `probe_ok=False` is also cached (no torch-import retry per request). Keep torch lazy.
- **Refactoring `get_best_device()`** risks a regression in the hot loader path. Mitigation: keep its DirectML (`:220-227`) + ROCm-GFX side-effect (`_configure_rocm_if_needed`, `:139-162`) + XPU (`:211-218`) branches; only the family decision delegates to the probe; the probe itself never mutates env (only `get_best_device()` calls `_configure_rocm_if_needed`); covered by existing model-load tests + new caps tests.
- **Over-eager 400s** breaking power-users on exotic setups (DirectML/XPU). Mitigation: `cpu_fallback`/`cpu_only` never block (warn only); only `unavailable` returns 400/error-frame, matching the existing `is_available()` 400 at `generation.py:346`. DirectML hosts (which `get_best_device` returns as a `torch_directml` device string, not in the `DeviceFamily` enum) map to `family="cpu"` + a DirectML note so DirectML-only Windows users get `cpu_only` (neutral), never spurious `unavailable` (cross-platform-parity guard).
- **Missed synth entry point** would re-introduce a silent fallback through the back door. Mitigation: §7 enumerates all three (REST, OpenAI-compat, WS) plus the dub ASR channel; tests cover each. If a fourth audio-producing path is added later, this spec's pattern (resolve backend → `resolve_routing` → block `unavailable`/surface `cpu_fallback`) is the contract to copy.
- **Header encoding crash** on a non-ASCII device name in `X-VoiceStudio-Routing-Reason`. Mitigation: `scrub_text` then ASCII-sanitize + length-cap (§7); test with a mocked non-ASCII device name.
- **`scrub_text(None) → ""` collapsing a JSON `null`** into an empty string for the "no reason" case, breaking the `routing_reason: str | null` contract. Mitigation: every serialization site uses `scrub_text(r) if r else None` (§5/§7); asserted in `test_routing_redaction.py`.
- **Dropping `gpu_routing` via the `extra="allow"` trap** — `PreflightResponse` does not set `extra="allow"`, so a stray dict-passthrough would be silently dropped. Mitigation: `gpu_routing` is an explicit `GpuRouting | None` field (§API); `test_setup_preflight.py` round-trips the model to assert it survives.
- **Redaction gap regression** — using `_mask_hf_tokens` (HF-only) for routing reasons would leak a GitHub/OpenAI token or home path interpolated from an exception message. Mitigation: routing reasons use `scrub_text` (the broader scrubber); `test_routing_redaction.py` asserts every credential shape + home path is redacted across payload/frame/header (local-first acceptance bar).
- **`hardware_probe` rebase regressing gguf quant selection** — `compute_class` bucketing is VRAM-driven and unchanged; only `backend` reporting gains `rocm`. Covered by leaving `_bucket()` (`hardware_probe.py:54-69`) intact and adding the rocm==cuda bucketing note. The MB↔GB unit conversion at the `device_caps` boundary is explicit (no silent unit drift in `hardware_probe`'s public `vram_mb`).

## PR slices

Each PR is independently green and shippable **continuous-to-main** (no RC, per the beta-cadence rule). Any PR that touches a doc these instructions cover updates that doc in the **same** PR.

1. **PR 1 — probe + resolver (backend-only, no behavior change):** `core/device_caps.py` (incl. `HostCaps` frozen dataclass with pinned fields, `detect_host_caps`/`refresh`/`mlx_supported` signatures, `probe_ok`, all §1a failure paths, no-network/no-new-regex), `services/engine_routing.py` (`RoutingResult` TypedDict + `resolve_routing` with all §2 rules incl. DirectML/XPU/degraded), refactor `get_best_device()` (`model_manager.py:195`) + `hardware_probe.detect_capabilities()` to delegate, unit tests (`test_device_caps.py`, `test_engine_routing.py`, `test_mlx_supported.py`, `test_routing_redaction.py`). No API/UI change.
2. **PR 2 — #390 MLX gate + ASR `gpu_compat` + IndexTTS2 fix + `rocm` audit:** shared `mlx_supported()`, MLX `is_available()` fixes (`tts_backend.py:579`, `asr_backend.py:507`), ABC `gpu_compat` on `ASRBackend` (`:37`), per-engine tuples, `IndexTTS2Backend` explicit tuple, regression tests.
3. **PR 3 — wire routing into `list_backends()` + `/engines` + select gating:** 4 routing keys in tts/asr/llm `list_backends` (single probe call, per-entry try/except, `scrub_text(r) if r else None` on routing reason + ASR `reason`), ASR full-parity fields, `select_engine` gating + new `SelectEngineResponse` model (`engines.py:270`, pinned 400 detail, LLM-skip, defensive `.get`), route-shape tests.
4. **PR 4 — preflight + diagnose + ALL synth-time gating:** `gpu_routing`/`engine_routing` in `/setup/preflight` (`wizard.py:203`, full §6 state table) + new `GpuRouting` model + explicit `PreflightResponse.gpu_routing` field + `DeviceInfo.gpu_family`/`vram_gb`, `_detect_gpu` rebase, routing-aware diagnose (`_check_device`/`_check_engines`), synth gating at REST (`generation.py` — `X-VoiceStudio-Routing*` headers), OpenAI-compat (`openai_compat.py`), and WS (`tts_stream.py` — `routing` frame) + dub ASR note, tests + docs/CHANGELOG (docs-sync).
5. **PR 5 — frontend matrix routing UI + synth toast:** `types.ts` fields (`EngineBackend` + `SelectEngineResponse`), `EngineCompatibilityMatrix.jsx` effective-device badge/highlight + all empty/unknown/legacy arms (§8), synth `X-VoiceStudio-Routing` toast + WS `routing`-frame handling (in-memory de-dup, no localStorage), i18n keys (all 21 locales incl. `routingUnknown`), vitest RTL + i18n-coverage gate.

## Acceptance criteria

- A single `detect_host_caps()` reports the correct family per host, **distinguishing ROCm from CUDA**, returns the exact `HostCaps` shape (per the §API examples), and `get_best_device()` / `hardware_probe.detect_capabilities()` agree with it. The probe **never raises** to a caller, **makes no network call**, and torch-unimportable degrades to a cached CPU-only `probe_ok=False` result while every downstream endpoint still responds (local-first).
- `resolve_routing` returns the typed `RoutingResult` (`effective_device`/`routing_status`/`routing_reason`) per the §2 rule order, never returns `"n/a"`, and is byte-identical for a given `HostCaps` across OSes.
- `/engines` returns `effective_device` + `routing_status` + `routing_reason` for **every** tts/asr/llm backend; ASR entries now carry the **full 11-key parity shape** (incl. `gpu_compat` + `install_hint`/`last_error`/`isolation_mode` + a scrubbed `reason`), and LLM entries carry `effective_device:"network"`/`routing_status:"n/a"`/`routing_reason:null`. A backend whose `is_available()` raises is reported `available:false`+`routing_status:"unavailable"`, not a 500. A `None` routing reason serializes as JSON `null`, not `""`.
- **#390 closed:** MLX-Audio and MLX-Whisper report `available=false` and never advertise a usable `mps` route on non-Apple-Silicon hosts (linux/x86_64, mac-Intel, Apple-Silicon-no-MPS), even with the package importable; `mlx_supported()` returns the exact pinned tuples.
- **IndexTTS2** no longer advertises CPU-only — it carries its real `gpu_compat` and routes correctly.
- **No silent CPU fallback on any path:** selecting or synthesizing with an engine that cannot use the host's GPU produces an explicit signal at **every** entry point — a 400 when the engine is `unavailable` on this host (REST `generate_speech`, OpenAI-compat `/v1/audio/speech`, select with the pinned detail) or a WS `{"type":"error"}` frame+close; and a surfaced `cpu_fallback` warning (matrix badge + `X-VoiceStudio-Routing` REST/OpenAI header + WS `{"type":"routing"}` frame + diagnose WARN + dub preflight note) when it lands on CPU despite the host having an accelerator the engine doesn't support.
- DirectML-only and XPU-only hosts never receive a spurious `unavailable`; they resolve to `cpu_only`/`cpu_fallback` (neutral/warn) with an explanatory reason (cross-platform parity).
- The Engine Compatibility Matrix shows, per engine, **the device it will actually use on this machine** (highlighted chip + status-toned badge with reason), suppresses the routing badge for unavailable engines, falls back to a neutral badge for unknown statuses, and renders legacy/no-routing payloads exactly as before.
- `/setup/preflight` and `/system/diagnose` include a routing verdict for the active engine covering all states (pass/warn/fail/no-active-engine/probe-failed); the `gpu_routing` object matches the `GpuRouting` model and **survives the `PreflightResponse` serializer** (explicit field, not dropped by absent `extra="allow"`); `device.gpu_family`/`device.vram_gb` are present.
- Synth-time HTTP headers `X-VoiceStudio-Routing`/`X-VoiceStudio-Routing-Reason` carry the exact pinned values, are emitted only for `cpu_fallback`/`accelerated`-with-caveat, are ASCII-safe (no latin-1 crash) and ≤256 chars; the WS `routing` frame matches the pinned schema.
- **Token/path safety:** every `routing_reason` (in the `/engines` payload, the WS frame, and the `X-VoiceStudio-Routing-Reason` header) passes through `core.scrub.scrub_text` (HF + GitHub + OpenAI + home-dir + secret-named env redaction), and header reasons are additionally ASCII-sanitized + length-capped. CodeQL clean — no new user-input-reachable regex (the only regexes in the path are the pre-existing bounded ones).
- **Localization:** all new user-facing strings go through i18n across all 21 locales (asserted by an i18n-coverage gate); no hardcoded CJK outside the translation layer (`test_no_hardcoded_cjk.py` green); GPU abbreviations stay in `GPU_LABEL` as functional identifiers.
- **Backward-compatible data:** no alembic/DB change and no migration (explicitly N/A — §API); no localStorage schema introduced; all API additions are additive/optional with old↔new client/server compatibility; on-disk model state untouched; `get_best_device()`'s string contract + side-effects preserved.
- **Versioning:** lands continuous-to-main across 5 independently-green slices on the open v0.3.x line; no version bump, no RC, no defer; #390 closed in-line.
- Default behavior is byte-identical in code path and payload shape across macOS/Windows/Linux; backend pytest + frontend vitest green; no new dependency; docs/CHANGELOG updated in the same PR as the user-visible change.
