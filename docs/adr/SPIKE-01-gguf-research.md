> **Archival note (2026-07-12):** moved here from `.planning/` in the root cleanup.
> Internal `.planning/` / `specs/` paths below are historical — those trees were removed; see git history.

# Phase 4: Adaptive & Specialty Engines (spike-first) — Research

## Licence correction (2026-10-03)

This correction supersedes the original commercial-compatibility conclusions below.
The [upstream OmniVoice card](https://huggingface.co/k2-fsa/OmniVoice#license)
distinguishes Apache-2.0 code from CC-BY-NC pretrained weights. The
[GGUF card](https://huggingface.co/Serveurperso/OmniVoice-GGUF#license) now lists
CC-BY-NC-4.0 for its converted weights. The
[singing card](https://huggingface.co/ModelsLab/omnivoice-singing) still advertises
Apache-2.0 and identifies OmniVoice as its base; that label does not resolve the
conflicting upstream terms. Commercial clearance for the singing weights remains
unverified. Tokenizer/component terms also require separate review; see
[LICENSE-NOTICE.md](../../LICENSE-NOTICE.md).

These historical engineering decisions do not approve any model for Pro. Model
rights must be recorded separately from runtime-code rights, with unresolved or
non-commercial weights excluded from Pro unless the necessary rights are obtained.
The required acceptance and backend enforcement work is tracked in #2587; this
correction does not claim those controls are already implemented.


**Researched:** 2026-05-18
**Domain:** TTS engine integration — quantized GGUF runtime + singing-voice variant, both descending from the same `k2-fsa/OmniVoice` lineage already shipping as VoiceStudio's default cloning engine
**Confidence:** HIGH for model identity and runtime requirements. Original licence and commercial GO/NO-GO conclusions are superseded by the correction above. MEDIUM for performance/latency numbers (no public benchmarks). MEDIUM for the heuristic singing/spoken segmentation strategy (SING-03 — feasible from existing toolkit but unbenchmarked).

---

## Summary

Both spike URLs are **real, live, and the intended artifacts**. The "VoiceStudio" name is not overloaded in the wild — both `Serveurperso/OmniVoice-GGUF` and `ModelsLab/omnivoice-singing` are direct descendants of `k2-fsa/OmniVoice` (the same upstream model VoiceStudio already ships as its default cloning engine). Their shared lineage does not establish commercial permission; see the licence correction above. Both use the **same Higgs Audio v2 codec at 24 kHz mono**, the same Qwen3-0.6B language model backbone, and the same overall architecture — they differ only in (a) quantization + runtime (GGUF/`omnivoice.cpp`) and (b) finetune dataset + emotion/singing control tags (ModelsLab).

**The framing changes once that's confirmed.** SPIKE-01 is not "adopt a new engine" — it is "ship a quantized runtime variant of the engine already inside VoiceStudio, selectable by hardware probe." SPIKE-02 is not "adopt a new engine architecture" — it is "ship a domain-adapted finetune of the same VoiceStudio model with singing-mode tags, callable through the existing `VoiceStudioBackend` API surface with a different `from_pretrained` ID."

**Primary recommendations:**

- **SPIKE-01 (OmniVoice-GGUF): technical GO**, conditional on a Phase 4-internal Apple Silicon `buildmetal.sh` smoke (no published Metal build script visible in `omnivoice.cpp/README.md`; only Vulkan and CPU are documented). The integration shape is `SubprocessBackend` wrapping the `omnivoice-tts` C++ CLI, with quant selected by a `detect_capabilities()` hardware probe.
- **SPIKE-02 (omnivoice-singing): GO with reduced scope**, treating it as a **second `from_pretrained` ID against the existing `VoiceStudioBackend`** rather than a new backend class — it is the same `omnivoice` PyPI library, same `transformers` pipeline, same model interface. The dubbing-pipeline "singing mode" toggle (SING-02) and Demucs vocal-stem routing (SING-03) remain real work, but they're pipeline integration, not engine integration. Net: 5 SING-* requirements stay in scope; SING-01's framing simplifies.

**Phase 2 dependency:** Both engines build on the `SubprocessBackend` primitive from Phase 2. Phase 4 cannot finalize PLAN.md until Phase 2 RESEARCH.md exists and confirms the subprocess + venv + `mp.get_context("spawn")` + `HF_HOME` inheritance contract. **Capture this as a planner gate, not as research blocked-on-Phase-2** — research can proceed using `SubprocessBackend` as a stable contract (the ROADMAP and SUMMARY both define it). PLAN.md cannot reference its internals until Phase 2 RESEARCH lands.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| GGUF model inference | API/Backend (subprocess) | OS/native binary (`omnivoice-tts`) | Subprocess wrapper around C++ CLI; no Python-level inference work inside VoiceStudio's main process |
| Hardware probe + quant selection | API/Backend | Frontend (Settings UI surface) | `detect_capabilities()` extension lives in `backend/services/`; UI just renders the result |
| Quant override UI | Frontend | API/Backend (persistence) | User-facing one-click switch; backend stores choice in SQLite settings |
| Singing model inference | API/Backend (in-process via `omnivoice` pip lib) | — | Drop-in alternative `from_pretrained` ID on existing `VoiceStudioBackend`; no new tier |
| Dubbing pipeline "singing mode" toggle | API/Backend (dub_pipeline.py extension) | Frontend (dub-job UI) | Routing logic lives in dubbing service; UI exposes the toggle |
| Speech vs singing segment auto-detect | API/Backend (signal-processing heuristic) | — | Pitch-stability + energy on Demucs vocal stem; pure backend |
| Decision documents | Repo metadata | — | `.planning/decisions/*.md` ADRs — neither tier owns these |

---

## Standard Stack

### Core — SPIKE-01 (OmniVoice-GGUF) — GO path

| Library / artifact | Version | Purpose | Why Standard |
|-----------|---------|---------|--------------|
| `omnivoice.cpp` (binary built from source) | head of `master` at integration time, pinned by commit SHA | GGUF inference runtime — C++17/GGML port of VoiceStudio | The **only** runtime that loads `Serveurperso/OmniVoice-GGUF` quants. No llama.cpp compatibility despite GGUF format (custom arch). MIT-licensed, by the same author who published the quants. |
| `Serveurperso/OmniVoice-GGUF` model files | pinned by commit SHA in `quant_map.json` | Pre-quantized weights | 4 quants × 2 files (base + tokenizer): Q4_K_M (~659 MB VRAM), Q8_0 (~945 MB, **recommended default**), BF16 (~1.6 GB), F32 (~3.2 GB) |
| `huggingface_hub` (already pinned) | ≥1.12.x | Quant file download | Reuses existing HF token + cache infrastructure (per Phase 1 token resolver) |
| `SubprocessBackend` (from Phase 2) | n/a | Engine isolation primitive | Wraps `omnivoice-tts` CLI as a managed subprocess with per-engine venv (the venv here only contains `huggingface_hub` for download — the inference binary is native) |

### Core — SPIKE-02 (omnivoice-singing) — GO path

| Library / artifact | Version | Purpose | Why Standard |
|-----------|---------|---------|--------------|
| `omnivoice` (PyPI, already a dep) | 0.1.5 (verified 2026-04-28) | VoiceStudio inference library — same one VoiceStudio already uses for `k2-fsa/OmniVoice` | Identical API surface; just a different `from_pretrained` ID. **No new dep.** |
| `ModelsLab/omnivoice-singing` model | pinned by commit SHA | Singing-finetuned weights | Reuses HF token + cache, ~same size as base model |
| Existing `VoiceStudioBackend` in `tts_backend.py` | n/a | Engine adapter | New subclass `VoiceStudioSingingBackend(VoiceStudioBackend)` overriding `from_pretrained` ID + `display_name` + emitting `[singing]` control tag in `generate()`. Same `SubprocessBackend` host pattern Phase 2 ships. |
| Demucs (already pinned) | already pinned | Vocal-stem isolation | Already used in dubbing pipeline; no new dep. Routes vocal stem → singing engine, instrumental stem preserved untouched. |
| `numpy` + `librosa`-equivalents already in deps | already pinned | Pitch-stability + energy heuristic for SING-03 segment detection | No new deps; segment boundaries computed from existing Demucs output |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `omnivoice.cpp` for GGUF inference | llama.cpp directly | **Rejected.** Quants use `omnivoice-lm` architecture, not standard llama. Won't load in vanilla llama.cpp; would require maintaining a fork. |
| `omnivoice.cpp` for GGUF inference | ONNX export + onnxruntime | **Rejected.** No published ONNX path for VoiceStudio's diffusion-LM hybrid; would require us to do the conversion work upstream. Out of scope for v0.3.x. |
| New `SingingBackend` class | Reuse `VoiceStudioBackend` with different model ID + control tag | **Recommended.** Same library, same architecture, same codec — code duplication would be 95% verbatim. A subclass overriding 3 attributes is the lower-risk shape. |
| Model-based singing-vs-speech classifier | Pitch-stability + energy heuristic per Demucs vocal stem | **Recommended for v0.3.** REQUIREMENTS.md already defers model classifier to v2. Heuristic is sufficient because users can override per segment in the dubbing UI. |
| Bundle `omnivoice-tts` binary in installer | Build at install time via `bootstrap.rs` extension | **Bundle** for v0.3.x. Building from source at install adds a C++ toolchain dep we don't otherwise need. Per-platform binaries (3-4 MB compressed) ship alongside the installer; mirror-cascade work from Phase 3 already gives us a graceful fallback path for restricted networks. |

**Installation (no new PyPI deps for the singing path; one build/bundle step for GGUF):**

```bash
# Singing variant — zero new deps
# Just adds a model entry; VoiceStudioSingingBackend reuses the existing omnivoice library

# GGUF variant — adds a bundled binary, not a Python dep
# Built once per platform in CI and stored alongside the Tauri installer:
#   bin/omnivoice-tts-{darwin-arm64,darwin-x86_64,windows-x86_64,linux-x86_64,linux-aarch64}
# Quants pulled at first use via huggingface_hub
```

**Version verification (verified 2026-05-18):**

```bash
# Confirmed live on PyPI (verified 2026-05-18 via WebFetch):
#   omnivoice 0.1.5 — released 2026-04-28, Apache-2.0
# Already a transitive/direct dependency of VoiceStudio (per tts_backend.py:VoiceStudioBackend)

# Confirmed live on HuggingFace (verified 2026-05-18 via WebFetch):
#   Serveurperso/OmniVoice-GGUF — 10,603 downloads last month, 4 quant variants
#   ModelsLab/omnivoice-singing — historical download count; commercial rights unresolved
#   k2-fsa/OmniVoice (upstream) — separate code/weight terms; arXiv 2604.00688

# Confirmed live on GitHub (verified 2026-05-18 via WebFetch):
#   ServeurpersoCom/omnivoice.cpp — MIT, 38 stars, 59 commits on master, 6 open issues
```

## Package Legitimacy Audit

| Package / artifact | Registry | Source Repo | slopcheck | Disposition |
|---------|----------|-------------|-----------|-------------|
| `omnivoice` | PyPI | github.com/k2-fsa/OmniVoice | not run (already a project dep) | Approved — already shipping in v0.2.7 |
| `Serveurperso/OmniVoice-GGUF` | HuggingFace | github.com/ServeurpersoCom/omnivoice.cpp | n/a (HF model, not a code package) | Approved [ASSUMED — verified via WebFetch of model card + linked source repo; not run through slopcheck because slopcheck targets PyPI/npm] |
| `ModelsLab/omnivoice-singing` | HuggingFace | derives from `k2-fsa/OmniVoice` | n/a (HF model) | Approved [ASSUMED — verified via WebFetch; lineage chain confirmed end-to-end] |
| `omnivoice.cpp` binary | GitHub source build | github.com/ServeurpersoCom/omnivoice.cpp | n/a (source build, not a package) | Approved with build-and-pin step in Phase 4 plan [ASSUMED — repo is small (38 stars, 59 commits, 6 open issues) — Phase 4 plan must add a `checkpoint:human-verify` task to inspect the commit being pinned before adopting] |

**Packages removed:** none
**Packages flagged as suspicious:** none formally — but `omnivoice.cpp` warrants a planner-inserted `checkpoint:human-verify` because it's a low-maturity project (38 stars). The verification step is "inspect the pinned commit SHA for obvious issues before bundling; ensure SHA matches what we built and tested."

*slopcheck was not run in this research session for the HF/GitHub artifacts because slopcheck targets code-package registries (PyPI/npm/crates) — HuggingFace model hashes and source builds need manual provenance review instead, which was performed via WebFetch of model cards + linked repos.*

---

## Architecture Patterns

### System Architecture Diagram — SPIKE-01 (GGUF, GO path)

```
                                     ┌──────────────────────────────────────┐
User opens app / first run           │  Settings → Engines → Default        │
        │                            │  ┌──────────────────────────────┐    │
        ▼                            │  │ Auto-selected quant: Q8_0    │    │
┌──────────────────────────┐         │  │ [▼ Override: F32/BF16/Q4_K_M]│    │
│ detect_capabilities()    │ ─────►  │  └──────────────────────────────┘    │
│   - CPU / RAM            │         │  Source of truth: SQLite settings    │
│   - GPU + VRAM (MB)      │         └──────────────────────────────────────┘
│   - compute_class bucket │                          │
│     (CPU/low/mid/high)   │                          │ persisted choice
└──────────────────────────┘                          ▼
        │                            ┌──────────────────────────────────────┐
        │ probe result               │ quant_map.json (shippable, not       │
        ▼                            │ pinned-in-binary):                   │
┌──────────────────────────┐         │   CPU       → Q4_K_M                 │
│ Quant resolver           │ ◄────── │   low-VRAM  → Q4_K_M (≤4 GB VRAM)    │
│ (compute_class → quant)  │         │   mid-VRAM  → Q8_0  (4-12 GB)        │
└──────────────────────────┘         │   high-VRAM → BF16  (≥12 GB)         │
        │                            └──────────────────────────────────────┘
        │ selected quant
        ▼
┌──────────────────────────┐         ┌──────────────────────────────────────┐
│ huggingface_hub.download │ ─────►  │ $HF_HUB_CACHE/                       │
│   Serveurperso/OmniVoice-GGUF      │   omnivoice-base-{Q}.gguf            │
│   pinned-by-SHA          │         │   omnivoice-tokenizer-{Q}.gguf       │
└──────────────────────────┘         └──────────────────────────────────────┘
        │                                          │
        ▼                                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ SubprocessBackend (Phase 2 primitive)                                   │
│                                                                         │
│  spawn:  bin/omnivoice-tts-{darwin-arm64|x86_64|win|linux|              │
│                              linux-aarch64}                             │
│          --model    $HF_HUB_CACHE/.../omnivoice-base-{Q}.gguf           │
│          --codec    $HF_HUB_CACHE/.../omnivoice-tokenizer-{Q}.gguf      │
│          --lang     <user lang>                                         │
│          --ref-wav  <voice clone reference, optional>                   │
│          --ref-text <voice clone reference text, optional>              │
│          -o         /tmp/omnivoice-job-<id>.wav                         │
│          < <stdin: prompt text>                                         │
│                                                                         │
│  stdout/stderr → backend.log (filtered for HF token per AUTH-05)        │
│  output file   → read back into torch.Tensor for downstream consumers   │
└─────────────────────────────────────────────────────────────────────────┘
```

### System Architecture Diagram — SPIKE-02 (Singing, GO path)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Dubbing job UI — new "Singing mode" toggle in job-creation form         │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ dub_pipeline.py (existing) — extended with singing routing              │
│                                                                         │
│  Source audio  ──► Demucs (already in pipeline)                         │
│                    ├── vocals stem ────┐                                │
│                    └── instrumental ───┼──► preserved untouched         │
│                                        │    in final mix                │
│                                        ▼                                │
│                    Segment detector (NEW, SING-03 heuristic):           │
│                      pitch stability  + energy                          │
│                      → list[(start, end, kind ∈ {speech, sing})]        │
│                                        │                                │
│                                        ▼                                │
│                    Route segments:                                      │
│                      kind=speech → VoiceStudioBackend (existing default)  │
│                      kind=sing   → VoiceStudioSingingBackend (NEW)        │
│                                        │                                │
│                                        ▼                                │
│                    Re-attach to instrumental stem                       │
│                                        │                                │
│                                        ▼                                │
│                              Final dubbed mix                           │
└─────────────────────────────────────────────────────────────────────────┘

VoiceStudioSingingBackend (NEW class, ≤ 30 lines):
  class VoiceStudioSingingBackend(VoiceStudioBackend):
      id            = "omnivoice-singing"
      display_name  = "VoiceStudio (singing)"
      model_id      = "ModelsLab/omnivoice-singing"
      def generate(self, text, **kw):
          text_with_tag = f"[singing] {text}" if not text.startswith("[") else text
          return super().generate(text_with_tag, **kw)
```

### Recommended Project Structure

```
backend/engines/
├── omnivoice_gguf/
│   ├── __init__.py
│   ├── backend.py            # VoiceStudioGGUFBackend(TTSBackend) — SubprocessBackend host
│   ├── quant_map.json        # compute_class → quant filename (updatable without release)
│   ├── hardware_probe.py     # extends services/gpu_sandbox.py probe
│   └── README.md             # engine card content (license, source URL, hardware notes)
└── omnivoice_singing/
    ├── __init__.py
    ├── backend.py            # VoiceStudioSingingBackend(VoiceStudioBackend) — subclass
    ├── segment_detector.py   # SING-03 pitch + energy heuristic
    └── README.md             # engine card content

backend/services/
├── tts_backend.py            # existing — register both new backends in _REGISTRY
└── dub_pipeline.py           # existing — extend with singing routing (SING-02, SING-03)

bin/                          # bundled in installer (per platform)
├── omnivoice-tts-darwin-arm64
├── omnivoice-tts-darwin-x86_64
├── omnivoice-tts-windows-x86_64.exe
├── omnivoice-tts-linux-x86_64
└── omnivoice-tts-linux-aarch64

.planning/decisions/
├── SPIKE-01-gguf.md          # ADR (this research → human review → ratified)
└── SPIKE-02-singing.md       # ADR
```

### Pattern 1: Hardware-Adaptive Default Engine (GGUF-05)

**What:** First-launch hardware probe runs `detect_capabilities()`, picks the quant from `quant_map.json`, sets OmniVoice-GGUF as the cloning default. User can override in Settings → Engines → Default.
**When to use:** Only on hardware where the probe succeeds AND the quant loads cleanly. Failure modes fall back to the pre-existing `VoiceStudioBackend` (the in-process Python path) — never to "no engine available."

```python
# Source: synthesized from Phase 2 SubprocessBackend contract + GGUF-01..05 requirements
# Confidence: HIGH for shape, MEDIUM for default-quant boundaries (no published benchmark)

def select_default_engine() -> str:
    """Run at first launch (and on Settings → Engines → 'Re-probe hardware')."""
    caps = detect_capabilities()  # extends backend/services/gpu_sandbox.py

    # Hardware bucket → quant from quant_map.json (shippable, not pinned in code)
    quant_map = json.load(open(GGUF_DIR / "quant_map.json"))
    quant = quant_map.get(caps.compute_class)  # one of: Q4_K_M, Q8_0, BF16, F32

    if quant is None:
        # CPU-only on a 4 GB-RAM box, etc. — fall back gracefully
        return "omnivoice"  # the existing in-process default

    # Probe load (sub-second timeout) — if the runtime binary or quant won't load,
    # don't make it default; keep falling back to the in-process VoiceStudioBackend
    try:
        VoiceStudioGGUFBackend.probe_load(quant=quant, timeout=5.0)
        return "omnivoice-gguf"
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("GGUF probe failed; falling back to in-process VoiceStudio: %s", e)
        return "omnivoice"
```

### Pattern 2: Subprocess CLI Wrapping (`omnivoice-tts`)

**What:** Each `generate()` call spawns the bundled `omnivoice-tts` binary with model + codec + reference args, reads the output WAV file back into a `torch.Tensor`. Lives in `SubprocessBackend`'s isolation venv from Phase 2.
**When to use:** Every GGUF generation. The C++ binary process itself is the isolation boundary; the per-engine "venv" here is essentially empty (just `huggingface_hub` for downloading quants), because the binary's deps are linked-in.

```python
# Source: omnivoice.cpp README CLI invocation [VERIFIED via WebFetch 2026-05-18]
# Combined with SubprocessBackend contract from Phase 2 ROADMAP

class VoiceStudioGGUFBackend(TTSBackend):
    id = "omnivoice-gguf"
    display_name = "VoiceStudio (GGUF)"

    @property
    def sample_rate(self) -> int:
        return 24_000  # Higgs Audio v2, same as base VoiceStudio

    def generate(self, text, *, ref_audio=None, ref_text=None, language="en", **kw):
        out_path = self._tmp_wav_path()
        args = [
            self._binary_path,                           # bin/omnivoice-tts-<plat>
            "--model",  str(self._base_quant_path),      # omnivoice-base-Q8_0.gguf
            "--codec",  str(self._tokenizer_quant_path), # omnivoice-tokenizer-Q8_0.gguf
            "--lang",   _iso_to_omnivoice_lang(language),
            "-o",       str(out_path),
        ]
        if ref_audio:
            args += ["--ref-wav", ref_audio]
            if ref_text:
                args += ["--ref-text", ref_text]

        # SubprocessBackend handles env scrubbing (HF_TOKEN injection from token_resolver,
        # log redaction per AUTH-05, mp.get_context("spawn") IPC supervision)
        self._subprocess_backend.run(args, stdin=text, timeout=120)

        wav, sr = soundfile.read(out_path)
        return torch.from_numpy(wav).unsqueeze(0)  # (1, n_samples)
```

### Anti-Patterns to Avoid

- **Loading GGUF via vanilla llama.cpp Python bindings (`llama-cpp-python`).** The quants use a custom `omnivoice-lm` architecture; only `omnivoice.cpp` parses them. Trying `llama-cpp-python` will silently load random tokens or fail with cryptic errors.
- **Building `omnivoice.cpp` at install time.** Adds a C++17 toolchain dep to every user's machine and breaks on Russia/China mirror failures (Phase 3 territory we can't re-litigate). Bundle prebuilt binaries per platform in the installer; CI does the building.
- **Treating `omnivoice-singing` as a totally separate engine class.** It is the same library, same architecture, same codec, same API; only the model ID and a `[singing]` text tag differ. A fresh `SingingBackend(TTSBackend)` would be 95% copy-paste of `VoiceStudioBackend` with one constant changed. Subclass instead.
- **Auto-routing singing mode without user consent.** SING-03 heuristic detection is correct as a *default routing suggestion*; per-segment override must be available in the dubbing UI before any segment is committed to a singing-engine render. The user owns the final route.
- **Bundling `omnivoice-tts` without code-signing on macOS.** macOS Sequoia + Gatekeeper will quarantine an unsigned binary the same way it quarantines the unsigned `.app` (REL-05 already tracks the cert work; until that ships, the same `xattr -cr` workaround from Phase 1 applies — surface it in the same error UI path).
- **Re-downloading quants on every launch.** Quants live in `$HF_HUB_CACHE`. The download check is "is the SHA-pinned file present" — never "always re-download for safety."

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| GGUF inference | A custom `llama.cpp` fork that adds the `omnivoice-lm` arch | `omnivoice.cpp` binary (upstream, MIT, by the quant author) | The author of the quants is the author of the runtime. Forking llama.cpp ourselves would add a permanent maintenance burden and zero capability over upstream. |
| Singing voice cloning model architecture | A trained-from-scratch singing model | `ModelsLab/omnivoice-singing` (same arch, finetuned) | The finetune exists and reuses the current runtime; commercial rights remain unresolved. |
| Vocal stem isolation for singing-mode routing | A custom source-separation network | Demucs (already in dubbing pipeline) | Already a dep, already integrated, results are good enough for routing decisions. |
| Speech-vs-singing classifier | A trained classification model for SING-03 v0.3 | Pitch-stability + energy heuristic on Demucs vocal stem; per-segment user override in UI | REQUIREMENTS.md already defers model-based classifier to v2. Heuristic is sufficient with override. |
| Hardware probe / VRAM bucketing | A new GPU-detection library | Extend the existing `backend/services/gpu_sandbox.py` probe | Already detects CUDA/MPS/ROCm/CPU; just adds VRAM bucketing on top. |
| GGUF quant download manager | A custom file mirror + resume | `huggingface_hub` with `$HF_HUB_CACHE` | Already pinned, already handles tokens/mirrors/resume/SHA verification. |

**Key insight:** Phase 4 is almost entirely **plumbing existing tools together** — every architectural primitive already exists (`SubprocessBackend` from Phase 2, `huggingface_hub`, Demucs, `VoiceStudioBackend`, hardware probe). The only genuinely new artifact is the `omnivoice.cpp` binary bundled per platform, and that's source-available + MIT-licensed + 6 open issues / 59 commits / small enough to audit at the pinned commit.

---

## Runtime State Inventory

> Phase 4 is **additive** (new engines, new bundled binary, new model files in HF cache). Nothing is renamed. The inventory below documents new runtime state introduced by Phase 4 so future maintenance phases can find it.

| Category | New runtime state Phase 4 introduces | Action Required |
|----------|---------------------------------------|------------------|
| Stored data | `$HF_HUB_CACHE/Serveurperso--OmniVoice-GGUF/`, `$HF_HUB_CACHE/ModelsLab--omnivoice-singing/`, SQLite settings columns: `gguf_quant_override`, `engine_default` (already supports add) | New cache entries (auto-cleaned by HF cache management); SQLite migration via project's `init_db()` |
| Live service config | None — no external services involved | None |
| OS-registered state | None — no Task Scheduler, launchd, systemd entries | None |
| Secrets / env vars | Reuses `HF_TOKEN` from Phase 1 token resolver; no new secrets | None |
| Build artifacts / installed packages | `bin/omnivoice-tts-*` binaries bundled per platform; built in CI from pinned `omnivoice.cpp` commit SHA | Phase 0 CI matrix already runs cross-platform — extend it to build the four binaries and verify checksums per GATE-05 |

---

## Common Pitfalls

### Pitfall 1: `omnivoice.cpp` has no published macOS Metal build script

**What goes wrong:** The README lists `buildcpu.sh`, `buildcuda.sh`, `buildvulkan.sh`, `buildall.sh` — but no `buildmetal.sh` or `buildmacos.sh`. README *mentions* Metal in the feature description with no corresponding build command [VERIFIED via WebFetch 2026-05-18].
**Why it happens:** Project is at 38 stars / 59 commits / 6 open issues; likely a maintenance gap, not a fundamental block. GGML itself supports Metal, so the build should be a `cmake -DGGML_METAL=ON` flag away.
**How to avoid:** Phase 4 Wave 1 includes a "build `omnivoice-tts` for Apple Silicon Metal" task with explicit acceptance criteria. If that task fails or hits an upstream block, downgrade SPIKE-01 from "default cloning engine" to "opt-in alternative on CUDA + Linux/Windows AVX2" and leave the `VoiceStudioBackend` (in-process Python) as the macOS default. The decision doc must capture this.
**Warning signs:** macOS CI fails to produce a `omnivoice-tts-darwin-arm64` binary, or the binary produces all-zero or all-noise audio on M-series Macs.

### Pitfall 2: Quant file size on Q4_K_M is too small for VRAM budget — but inference quality drops

**What goes wrong:** "Q4_K_M fits in 4 GB VRAM, use it" reasoning leads to deploying Q4_K_M as the *default* on low-VRAM hardware; output quality is audibly worse than Q8_0 and users perceive the GGUF engine as a regression.
**Why it happens:** Quant maps usually optimize for *fit*, not for *quality given fit*. There is no published quality benchmark across quants for OmniVoice-GGUF (verified — model card does not provide MOS or perceptual scores).
**How to avoid:** Default to `Q8_0` for any hardware with ≥ ~1 GB available VRAM (covers 4 GB+ GPUs comfortably). Use Q4_K_M only on truly constrained hardware (≤ 2-3 GB VRAM, CPU-only). Make `quant_map.json` user-editable so audiophile users can force BF16/F32. Surface the trade in the Settings UI: "Auto-selected: Q8_0 (recommended). Override: …".
**Warning signs:** Smoke test on 8 GB VRAM macOS/Windows class (per GGUF-06) produces audibly worse output than the in-process `VoiceStudioBackend`.

### Pitfall 3: `omnivoice-tts` binary is unsigned, gets quarantined on macOS Sequoia

**What goes wrong:** User downloads VoiceStudio installer, app launches but every GGUF generation fails with "cannot verify developer."
**Why it happens:** Same Gatekeeper path as #54 (the `.app` quarantine); the bundled binary inside is *also* unsigned and *also* quarantined.
**How to avoid:** Treat the `bin/omnivoice-tts-darwin-*` binaries as part of the same `xattr -cr` workaround surface as #54. Extend the macOS quarantine-detection probe in `docs/install/macos.md` and the error UI from Phase 1 to also clear the binary's xattr on first launch (or instruct the user to). Track real signing under REL-05's tracking-issue plan.
**Warning signs:** macOS users report "GGUF works on Linux, breaks on Mac with no error message" — that's Gatekeeper silently killing the spawn.

### Pitfall 4: `omnivoice-singing` model returns garbled output when `[singing]` tag is missing

**What goes wrong:** Calling `omnivoice-singing` without the `[singing]` control tag produces nonsense or near-silent output; user reports "the singing engine is broken."
**Why it happens:** The finetune was trained on tagged data; the tag is the gate.
**How to avoid:** `VoiceStudioSingingBackend.generate()` injects `[singing]` automatically unless the prompt already starts with a `[`-prefixed tag (so power users can compose `[singing] [happy]` etc. manually). Surface the supported tag set in the engine card README and Settings.
**Warning signs:** First end-to-end singing-mode dubbing test produces speech-like output instead of melodic output.

### Pitfall 5: SING-03 heuristic misclassifies operatic / sustained-vowel speech as "speech" (or vibrato speech as "singing")

**What goes wrong:** Pitch-stability heuristic flags a singer holding a vowel as "speech" because pitch is *too* stable; or flags a vibrato-heavy actor as "singing" because pitch fluctuates.
**Why it happens:** The heuristic is one-dimensional. Real speech vs singing is multi-feature (rhythm, harmonic structure, vibrato pattern).
**How to avoid:** Treat heuristic output as a *routing suggestion*, never a *commit*. Show predicted segments in the dubbing UI with per-segment override (REQUIREMENTS SING-03 already requires "power-user override available per segment"). Acceptance criterion in SING-05 is "consistent voice identity across spoken and sung segments" — that's testable independent of classifier accuracy.
**Warning signs:** Smoke test (SING-05) produces a final mix where segments are routed correctly but the user has to manually fix 5+ segments per 30 seconds.

### Pitfall 6: Phase 4 PRs accidentally regress Phase 3's mirror reliability work

**What goes wrong:** Bundling `omnivoice-tts` binaries in the installer changes the installer manifest; if not done carefully, Phase 3's mirror-cascade tests assume binary-size-X, and the regression suite breaks.
**Why it happens:** ROADMAP explicitly notes Phase 4 can run in parallel with Phase 3 *but must not regress mirror reliability*. The natural failure mode is "installer size grew, network smoke test on the slow-mirror profile times out."
**How to avoid:** GATE-03 (release.yml installer smoke test) must run with the bundled binaries before any Phase 4 PR merges. The four-binary bundle adds ~12-16 MB compressed — well within the existing installer size envelope, but verify against the Phase 3 mirror-timing baseline.
**Warning signs:** Phase 3's mirror-cascade test starts timing out on the Tsinghua/Aliyun-only network profiles right when Phase 4 PRs land.

---

## Code Examples

### Hardware probe extension (GGUF-01)

```python
# Source: extends backend/services/gpu_sandbox.py (already detects CUDA/MPS/ROCm/CPU)
# Confidence: HIGH for shape (existing pattern); MEDIUM for the exact VRAM thresholds
#             (no published OmniVoice-GGUF quality-per-VRAM benchmark)

from dataclasses import dataclass
from typing import Literal

ComputeClass = Literal["cpu", "low-vram", "mid-vram", "high-vram"]

@dataclass(frozen=True)
class HardwareCapabilities:
    backend: Literal["cuda", "mps", "rocm", "cpu"]
    vram_mb: int          # 0 for CPU/MPS-unified-memory bookkeeping; MPS reports via mps.current_allocated_memory()
    compute_class: ComputeClass

def detect_capabilities() -> HardwareCapabilities:
    if torch.cuda.is_available():
        free, total = torch.cuda.mem_get_info()  # bytes
        vram_mb = total // (1024 * 1024)
        return HardwareCapabilities(
            backend="cuda",
            vram_mb=vram_mb,
            compute_class=_bucket(vram_mb),
        )
    if torch.backends.mps.is_available():
        # MPS unified memory: treat half of system RAM as effective VRAM ceiling
        ram_mb = psutil.virtual_memory().total // (1024 * 1024)
        vram_mb = ram_mb // 2
        return HardwareCapabilities(
            backend="mps",
            vram_mb=vram_mb,
            compute_class=_bucket(vram_mb),
        )
    # CPU
    return HardwareCapabilities(backend="cpu", vram_mb=0, compute_class="cpu")

def _bucket(vram_mb: int) -> ComputeClass:
    if vram_mb >= 12_000:  return "high-vram"   # → BF16
    if vram_mb >=  4_000:  return "mid-vram"    # → Q8_0  (recommended default)
    if vram_mb >=  1_000:  return "low-vram"    # → Q4_K_M
    return "cpu"                                # → Q4_K_M (CPU inference)
```

### `quant_map.json` (GGUF-02)

```json
// Source: synthesized from OmniVoice-GGUF model card quant table + recommended-defaults heuristic
// Shippable JSON so the table can update without an app release
{
  "_meta": {
    "schema_version": 1,
    "source_model": "Serveurperso/OmniVoice-GGUF",
    "source_commit_sha": "<PIN-AT-PHASE-4-PLAN-TIME>",
    "runtime": "omnivoice.cpp",
    "runtime_commit_sha": "<PIN-AT-PHASE-4-PLAN-TIME>"
  },
  "high-vram": {
    "base": "omnivoice-base-BF16.gguf",
    "tokenizer": "omnivoice-tokenizer-BF16.gguf",
    "rationale": "≥12 GB VRAM — quality-first; ~1.6 GB total VRAM use"
  },
  "mid-vram": {
    "base": "omnivoice-base-Q8_0.gguf",
    "tokenizer": "omnivoice-tokenizer-Q8_0.gguf",
    "rationale": "4-12 GB VRAM — recommended balance; ~945 MB VRAM use"
  },
  "low-vram": {
    "base": "omnivoice-base-Q4_K_M.gguf",
    "tokenizer": "omnivoice-tokenizer-Q4_K_M.gguf",
    "rationale": "1-4 GB VRAM — minimal footprint; ~659 MB VRAM use"
  },
  "cpu": {
    "base": "omnivoice-base-Q4_K_M.gguf",
    "tokenizer": "omnivoice-tokenizer-Q4_K_M.gguf",
    "rationale": "CPU-only — Q4_K_M to keep latency tolerable"
  }
}
```

### Segment detector skeleton (SING-03 heuristic)

```python
# Source: synthesized from REQUIREMENTS.md SING-03 ("pitch-stability + energy heuristic")
# Confidence: MEDIUM — heuristic is feasible from existing toolkit (librosa-style pitch + RMS),
#             but no published benchmark for this exact heuristic on VoiceStudio's dubbing corpus.
#             User override per segment is the safety net.

from dataclasses import dataclass
from typing import Literal

SegmentKind = Literal["speech", "sing"]

@dataclass(frozen=True)
class Segment:
    start_s: float
    end_s: float
    kind: SegmentKind
    confidence: float  # 0..1 — surface in UI for user override

def detect_singing_segments(
    vocal_stem_wav: "np.ndarray",
    sr: int,
    *,
    min_segment_s: float = 1.0,
) -> list[Segment]:
    """
    Per-frame analysis on the Demucs vocal stem:
      • pitch via librosa.yin (or torch equivalent already in deps)
      • energy via RMS
    Sing-flag if pitch is sustained > N frames AND energy is above threshold.
    Merge adjacent flagged frames into segments ≥ min_segment_s.
    Return segments with a coverage gap = inferred speech segment.
    """
    ...  # implementation in segment_detector.py
```

### Engine registration

```python
# Source: extends backend/services/tts_backend.py _REGISTRY (existing)
# Confidence: HIGH (existing pattern preserved)

# In tts_backend.py (additive):
class VoiceStudioGGUFBackend(TTSBackend):
    id = "omnivoice-gguf"
    display_name = "VoiceStudio (GGUF, hardware-adaptive)"
    # … see Pattern 2 above

class VoiceStudioSingingBackend(VoiceStudioBackend):
    id = "omnivoice-singing"
    display_name = "VoiceStudio (singing)"
    # See Architecture Diagram inset

_REGISTRY.update({
    "omnivoice-gguf":    VoiceStudioGGUFBackend,
    "omnivoice-singing": VoiceStudioSingingBackend,
})
```

---

## State of the Art

| Old approach | Current approach (2026) | When changed | Impact |
|--------------|--------------------------|--------------|--------|
| Single in-process model per app | Subprocess per engine with HF-cache sharing | Phase 2 of this milestone (forces the issue) | Phase 4 inherits this baseline cleanly; both new engines fit the pattern |
| GGUF + llama.cpp universal runtime | Model-architecture-specific GGUF runtimes (`omnivoice.cpp`, `whisper.cpp`, etc.) | 2025-2026 — as TTS/audio models adopted GGUF format with custom architectures | Can't reuse llama.cpp ecosystem for VoiceStudio quants; need the dedicated runtime. Locks us to `omnivoice.cpp` for as long as the quant format stays current. |
| `transformers` `text-to-speech` pipeline as common surface | Model-specific Python packages (`omnivoice`, `supertonic`, etc.) when models exceed the pipeline contract | Ongoing | omnivoice-singing model card *mentions* a `pipeline("text-to-speech", model="ModelsLab/omnivoice-singing")` path, but the existing VoiceStudio library is what we already ship — keep using it. |

**Deprecated/outdated:**
- Hand-rolling singing-vs-speech via spectral envelope alone (high false positives on sustained-vowel speech). Pair pitch-stability with energy, gate-and-override.
- Per-engine custom credential storage. Phase 1 token resolver supersedes; both new engines inherit `HF_TOKEN` from the resolver.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `omnivoice.cpp` Metal build path can be enabled via `cmake -DGGML_METAL=ON` or similar GGML flag | Pitfall #1 | If false: macOS Apple Silicon needs a Vulkan-via-MoltenVK path or in-process fallback, downgrading SPIKE-01 to "opt-in on non-Apple-Silicon" |
| A2 | Per-platform `omnivoice-tts` binary compressed size is ~3-4 MB / ~12-16 MB total | Pattern 2 / Pitfall #6 | If much larger: installer size grows enough to stress Phase 3's mirror timing budget; may need to fetch binaries at install time instead |
| A3 | Pitch-stability + energy heuristic is sufficient for SING-03 quality bar (with per-segment override) | Pitfall #5 | If false: SING-05 smoke test fails routinely → need to defer SING-03 model-based path to v2 even more explicitly, leaving v0.3 with manual segment marking only |
| A4 | `quant_map.json` thresholds (1 GB / 4 GB / 12 GB) match perceptual quality cliffs across hardware | Pattern 1, code example | If thresholds are wrong: users on borderline hardware see quality drops. Mitigation: ship as updatable JSON, plus Settings override |
| A5 | `huggingface_hub` SHA-pinned download produces deterministic file paths across `huggingface_hub` minor versions | Code Examples | If false: HF-CLI behavior changes break pin lookups. Mitigation: pin `huggingface_hub` minor version in `pyproject.toml` |
| A6 | Phase 2 `SubprocessBackend` supports passing CLI args + stdin + reading an output file (not just stdin/stdout streaming) | Pattern 2 | If Phase 2 designs stdin/stdout-only IPC: GGUF backend has to write text to a tempfile first or pipe and capture, minor refactor |
| A7 | macOS Gatekeeper xattr-clearing extends naturally to bundled child binaries | Pitfall #3 | If false: even after `xattr -cr .app`, the inner `omnivoice-tts` binary stays quarantined; user needs a second command. Documentable workaround. |
| A8 | `ModelsLab/omnivoice-singing` model is loadable via the same `omnivoice` PyPI library as the upstream `k2-fsa/OmniVoice` model | Stack — SPIKE-02 | If false: would need to invoke via `transformers` pipeline (model card mentions this works), forcing a third loading path. Low risk — both models share Qwen3-0.6B + Higgs Audio v2 architecture per model cards. |

---

## Open Questions

1. **Should the GGUF engine become the default for *all* hardware, or only for CPU + low-VRAM where it strictly wins over the Python in-process path?**
   - What we know: Q8_0 (~945 MB VRAM) is a small fraction of the in-process Python model footprint; latency could plausibly be faster on identical hardware. But there is no published latency benchmark.
   - What's unclear: Whether the in-process Python path on a 16 GB VRAM CUDA box outperforms Q8_0 in latency or quality. The naive expectation is "GGUF wins on CPU/low-VRAM, ties on mid-VRAM, in-process Python wins on high-VRAM."
   - Recommendation: Wave 1 of Phase 4 includes a head-to-head benchmark task across the three hardware classes called out in GGUF-06. The decision doc captures the resulting default policy. GGUF-05 already specifies "default on hardware that passes the probe with overridable fallback" — the open question is just where the "passes" boundary sits.

2. **Does `omnivoice-singing` quality on cross-lingual singing (per its own model card "extrapolation with variable quality") meet the SING-05 acceptance bar?**
   - What we know: Native-language singing is the trained case; cross-lingual is acknowledged as extrapolation.
   - What's unclear: Whether the SING-05 smoke test ("dub a 30-second mixed speech+singing source") will use same-language or cross-language singing.
   - Recommendation: SING-05 should explicitly run both same-language and cross-language singing in the test mix. NO-GO outcome here is partial: keep same-language singing GO, mark cross-language as v2.

3. **What's the right behavior when the GGUF probe succeeds but the user's network can't reach HF to download quants (China/Russia path from Phase 3)?**
   - What we know: Phase 3 establishes mirror cascade for the *Python install* (`UV_PYTHON_INSTALL_MIRROR`), not for model downloads. HF itself has a `HF_ENDPOINT` env var that routes to an HF mirror.
   - What's unclear: Whether Phase 4 inherits Phase 3's mirror choices for *model* downloads, or whether HF mirroring is a separate Phase 4 task.
   - Recommendation: Document this in the Phase 4 PLAN as "uses `HF_ENDPOINT` if set, otherwise default HF — does not introduce a new mirror cascade beyond what Phase 1 token resolver and Phase 3 install mirroring already provide." If quant download fails, fall back to in-process `VoiceStudioBackend` rather than blocking the engine.

4. **Does the existing dubbing pipeline already have a per-segment routing API, or does SING-02 require new pipeline infrastructure?**
   - What we know: `dub_pipeline.py` exists in `backend/services/`; details of its segment-routing surface require reading the file.
   - What's unclear: Whether singing-mode routing is a 50-line addition or a 500-line refactor.
   - Recommendation: First task in Phase 4 Wave 2 is a 1-hour code-read of `dub_pipeline.py` to scope SING-02 accurately. If the refactor is large, descope to "singing mode applies to *entire dubbing job* not per-segment" for v0.3 and defer per-segment to v0.4.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| `omnivoice` Python lib | SPIKE-02 backend (`VoiceStudioSingingBackend`) | ✓ (already pinned via base engine) | 0.1.5 | — |
| `huggingface_hub` | Both spikes (quant + singing-model download) | ✓ (Phase 1 dep) | ≥1.12.x | — |
| `omnivoice-tts` binary (built from `omnivoice.cpp`) | SPIKE-01 inference | ✗ at research time | — | Bundle in installer per platform; if Apple Silicon Metal build blocks, fall back to in-process `VoiceStudioBackend` on macOS |
| C++17 toolchain + CMake (for CI binary build only) | CI binary build job | ✓ (standard GitHub-hosted runners) | varies | — |
| `SubprocessBackend` primitive | Both spikes' engine isolation | ✗ at research time (Phase 2 dependency) | — | **Hard dependency** — Phase 4 PRs cannot merge until Phase 2 ships |
| Demucs | SING-02/SING-03 vocal-stem routing | ✓ (existing dubbing pipeline) | already pinned | — |
| Pitch-extraction lib (`librosa.yin` or torch equivalent) | SING-03 heuristic | ✓ (already in deps via Demucs/dubbing) | already pinned | — |
| macOS code signing cert | GGUF binary on macOS Sequoia | ✗ (REL-05 tracking issue, out of scope this milestone) | — | Same `xattr -cr` workaround as #54; documented in `docs/install/macos.md` |

**Missing dependencies with no fallback:** none (binary build is in our control via CI).
**Missing dependencies with fallback:** `omnivoice-tts` on Apple Silicon Metal — graceful degradation to in-process `VoiceStudioBackend` if the Metal build doesn't materialize.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest` (existing) + smoke scripts (`scripts/smoke-test.sh` pattern) |
| Config file | `pyproject.toml` (post-PR #50 scoped pytest) |
| Quick run command | `uv run pytest tests/engines/test_omnivoice_gguf.py tests/engines/test_omnivoice_singing.py -x` |
| Full suite command | `uv run pytest tests/engines/ tests/services/test_dub_pipeline_singing.py -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SPIKE-01 | Decision doc exists with GO/NO-GO + rationale; cited model card facts | doc-presence | `test -f .planning/decisions/SPIKE-01-gguf.md && grep -q 'Decision: GO\|Decision: NO-GO' .planning/decisions/SPIKE-01-gguf.md` | ❌ Wave 0 (this research is the input) |
| SPIKE-02 | Decision doc exists with GO/NO-GO + rationale | doc-presence | `test -f .planning/decisions/SPIKE-02-singing.md && grep -q 'Decision: GO\|Decision: NO-GO' .planning/decisions/SPIKE-02-singing.md` | ❌ Wave 0 |
| GGUF-01 | Hardware probe returns valid `HardwareCapabilities` for current platform | unit | `pytest tests/engines/test_hardware_probe.py::test_detect_capabilities_returns_valid_class -x` | ❌ Wave 0 |
| GGUF-02 | `quant_map.json` is valid JSON, every compute_class maps to existing quant filenames | unit | `pytest tests/engines/test_omnivoice_gguf.py::test_quant_map_valid -x` | ❌ Wave 0 |
| GGUF-03 | Backend can spawn `omnivoice-tts`, pass args, read output WAV | integration (requires binary) | `pytest tests/engines/test_omnivoice_gguf.py::test_subprocess_generate_3s -x` | ❌ Wave 0 |
| GGUF-04 | Settings → quant override persists to SQLite + reloads on next launch | integration | `pytest tests/services/test_settings_store.py::test_quant_override_round_trip -x` | ❌ Wave 0 |
| GGUF-05 | `select_default_engine()` returns `omnivoice-gguf` on hardware that passes probe, falls back on probe failure | unit (mocked probe) | `pytest tests/engines/test_omnivoice_gguf.py::test_default_selection -x` | ❌ Wave 0 |
| GGUF-06 | End-to-end clone 3s in 3 hardware classes — quant matches table, output intelligible | smoke (manual + CI matrix) | `scripts/smoke-gguf.sh --hardware-class {cpu,mid,high}` | ❌ Wave 0 (CI matrix integration) |
| SING-01 | `VoiceStudioSingingBackend` loads, generates 1s with `[singing]` tag auto-injected | unit | `pytest tests/engines/test_omnivoice_singing.py::test_generate_with_auto_tag -x` | ❌ Wave 0 |
| SING-02 | Dubbing pipeline routes vocal stem → singing engine when toggle is on; instrumental preserved | integration | `pytest tests/services/test_dub_pipeline_singing.py::test_singing_mode_preserves_instrumental -x` | ❌ Wave 0 |
| SING-03 | Segment detector returns valid `Segment` list with kind + confidence on a known mixed clip | unit | `pytest tests/services/test_segment_detector.py::test_mixed_clip_routing -x` | ❌ Wave 0 |
| SING-04 | License surfacing must distinguish code, weights and upstream restrictions; acceptance/enforcement remains to be implemented | unit + UI | `pytest tests/engines/test_omnivoice_singing.py::test_license_gate -x` | ❌ Wave 0 |
| SING-05 | 30s mixed speech+singing clip dubs end-to-end; both segments intelligible, instrumental preserved | smoke | `scripts/smoke-singing.sh tests/fixtures/mixed-30s.wav` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/engines/test_omnivoice_gguf.py tests/engines/test_omnivoice_singing.py -x`
- **Per wave merge:** Full suite (`uv run pytest tests/engines/ tests/services/test_dub_pipeline_singing.py -x`) + cross-platform smoke (`scripts/smoke-gguf.sh`, `scripts/smoke-singing.sh`)
- **Phase gate:** Full suite green + Phase 0 cross-platform CI matrix green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/engines/test_hardware_probe.py` — covers GGUF-01
- [ ] `tests/engines/test_omnivoice_gguf.py` — covers GGUF-02, GGUF-03, GGUF-05
- [ ] `tests/engines/test_omnivoice_singing.py` — covers SING-01, SING-04
- [ ] `tests/services/test_dub_pipeline_singing.py` — covers SING-02
- [ ] `tests/services/test_segment_detector.py` — covers SING-03
- [ ] `tests/services/test_settings_store.py::test_quant_override_round_trip` — extend existing test file for GGUF-04
- [ ] `scripts/smoke-gguf.sh` — covers GGUF-06 (parameterized by hardware class for CI matrix)
- [ ] `scripts/smoke-singing.sh` — covers SING-05
- [ ] `tests/fixtures/mixed-30s.wav` — 30-second mixed speech+singing test asset (must check in)

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Reuses Phase 1 `token_resolver` (HF token, no new auth surface) |
| V3 Session Management | no | No sessions — local-first desktop app |
| V4 Access Control | partial | Settings override for default engine + quant — same access-control surface as existing Settings panel (signed-in OS user only) |
| V5 Input Validation | yes | Validate quant filenames against `quant_map.json` allow-list before passing to subprocess; validate ref-audio paths stay within app-allowed directories |
| V6 Cryptography | partial | SHA-256 checksums on bundled `omnivoice-tts` binaries (per GATE-05); HF model files use HF's own SHA verification |

### Known Threat Patterns for {Phase 4 stack}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Bundled binary tampered post-install | Tampering | SHA-256 published in release body per GATE-05; verify at first launch and on every quant load |
| Arbitrary subprocess args injected via UI (e.g., `--ref-wav` controlled by user input) | Tampering / EoP | Subprocess args composed from typed `Path` objects rooted in app dirs; reject paths outside `omnivoice_data/` or `$HF_HUB_CACHE`; never pass shell=True |
| Malicious GGUF served from network MITM during quant download | Tampering | `huggingface_hub` enforces SHA verification on download; pin quant by commit SHA in `quant_map.json` |
| HF token leaked into `omnivoice-tts` subprocess stderr | Information Disclosure | AUTH-05 logging filter applies to subprocess stderr capture; subprocess inherits env via Phase 2 contract (no token in args) |
| Quant override UI used to load attacker-controlled GGUF path | Tampering / EoP | Quant override is a dropdown over `quant_map.json`-listed files only; no freeform path input (analogous to INST-09 mirror allow-list) |
| Dubbing pipeline writes generated audio with attacker-influenced filename | Tampering | Output paths constructed from job ID + temp dir; user-supplied names go through `os.path.basename` + character allow-list |

---

## Spike Methodology

### How this research executed the SPIKE protocol

1. **Web-fetch model cards** [DONE 2026-05-18]: Confirmed both `Serveurperso/OmniVoice-GGUF` and `ModelsLab/omnivoice-singing` exist, are public, are descendants of `k2-fsa/OmniVoice`, with shared upstream lineage. The original licence-compatibility conclusion is withdrawn; see the correction above and `huggingface.co/k2-fsa/OmniVoice`.
2. **Web-fetch runtime repo** [DONE 2026-05-18]: Confirmed `github.com/ServeurpersoCom/omnivoice.cpp` is the only runtime for the quants; documented MIT license, build script set, CLI invocation pattern, and maintenance state (38 stars, 59 commits, 6 open issues).
3. **PyPI package verification** [DONE 2026-05-18]: Confirmed `omnivoice` 0.1.5 on PyPI (2026-04-28, Apache-2.0) — already a project dep, no new dependency for SPIKE-02.
4. **Architecture compatibility check** [DONE]: Both downstream models share the same architecture as the project's existing default `VoiceStudioBackend` (Qwen3-0.6B + Higgs Audio v2 codec, 24 kHz mono). This is the load-bearing finding that re-frames both spikes from "new engines" to "variants of the engine already shipping."
5. **Integration-shape proposal** [DONE]: Documented both backend classes' shape, subprocess wiring, hardware probe extension, segment detector skeleton — all using existing project primitives.
6. **GO/NO-GO recommendation** [DONE]: Below, with explicit rationale.
7. **Decision doc templates** [provided below in `## Decision Doc Templates`]: Ready to copy into `.planning/decisions/SPIKE-01-gguf.md` and `SPIKE-02-singing.md` after human review.

### What this research deliberately did NOT do

- **Run the binary on the researcher's machine.** Spike code execution belongs in Phase 4 Wave 1 (`scripts/smoke-gguf.sh` per GGUF-06). Research stops at "evidence the integration shape is feasible and the artifacts are real."
- **Benchmark quality across quants.** No public benchmark exists; head-to-head MOS testing belongs in Wave 1's benchmark task and informs the final `quant_map.json` defaults.
- **Final-pin commit SHAs for `omnivoice.cpp` or the quants.** Pinning belongs in PLAN.md at the moment of integration, not in research — pins drift if research runs ahead of the PR.

---

## GO / NO-GO Recommendations

### SPIKE-01 (Serveurperso/OmniVoice-GGUF): **TECHNICAL GO**

This establishes technical feasibility only. Free-app acceptance also requires the rights gate in [engine acceptance](../engine-acceptance.md), including owner approval and first-use disclosure for restricted engines.

**Rationale:**
- Historical model/runtime/lineage review dated 2026-05-18; commercial clearance superseded above.
- ✓ Same underlying model as VoiceStudio's existing default — this is *quantization of what we already ship*, not a new engine architecture. Worst case it just doesn't beat the in-process Python path on a given hardware class, and we keep that path as the fallback.
- Commercial model clearance withdrawn; runtime and weight terms require separate records.
- ✓ Cross-platform via CUDA / Vulkan / Metal / CPU per `omnivoice.cpp` build matrix [with **Assumption A1** caveat — Metal build script not in published README, must validate Wave 1].
- ✓ Hardware adaptation is a natural fit for the user-stated value of "first-run that actually works" on a wide range of hardware — Q4_K_M ~659 MB VRAM gets the engine running on 4 GB GPUs that today fall back to CPU on the in-process Python path.

**GO conditions (must be true in PLAN.md):**
1. Phase 2 `SubprocessBackend` primitive lands first.
2. Wave 1 includes a build-and-verify task for the macOS Apple Silicon Metal binary; if that task fails, scope contracts to non-Apple-Silicon platforms with documented fallback to in-process `VoiceStudioBackend` on macOS.
3. `omnivoice.cpp` is pinned by commit SHA at integration time; quants pinned by commit SHA in `quant_map.json`.
4. Bundled binaries get the same `xattr -cr` workaround surfaced as #54.

### SPIKE-02 (ModelsLab/omnivoice-singing): **GO** with reduced scope ✓

**Rationale:**
- Historical model/runtime review dated 2026-05-18; commercial clearance superseded above.
- ✓ Same `omnivoice` PyPI library, same architecture, same codec — load-bearing finding: this is *not* a new engine, it's a new model ID consumed by the engine class we already have. `VoiceStudioSingingBackend` is a ≤30-line subclass.
- The former training-versus-use licence conclusion is withdrawn. The singing model requires upstream clarification before commercial approval.
- ✓ Hardware: same footprint as existing VoiceStudio; runs on existing-engine-compatible hardware.

**Scope reductions captured by this research:**
- SING-01: stays as written — the new backend class is small but real.
- SING-02: depends on `dub_pipeline.py` segment-routing surface — research recommends a code-read task as the first Wave 2 task to scope accurately; if the existing pipeline doesn't support per-segment routing, descope to "singing mode applies to entire dubbing job" for v0.3.
- SING-03: heuristic-only per existing v2-deferred decision — RESEARCH proposes the specific heuristic (pitch-stability + energy on Demucs vocal stem with per-segment override).
- SING-04 / SING-05: stay as written.

**GO conditions (must be true in PLAN.md):**
1. Phase 2 `SubprocessBackend` primitive lands first (consistency with the Phase 4 host pattern, even though the singing backend runs in-process via the existing `omnivoice` lib — the consistency matters for the engine registry).
2. SING-02 scope is decided after the Wave 2 `dub_pipeline.py` code-read.
3. SING-05 acceptance allows native-language singing pass + cross-language singing flagged as best-effort (per model-card "extrapolation with variable quality" disclaimer).

### Combined Phase 4 outcome

Both spikes GO → 13/13 requirements stay in scope. No requirements move to Out of Scope. Decision docs ratify these findings, planner builds plans against them, no REQUIREMENTS.md surgery needed.

---

## Decision Doc Templates

Two files, written by the planner after Phase 4 PLAN.md is locked, using this research as primary input. Skeleton templates provided here so the planner has them ready.

### `.planning/decisions/SPIKE-01-gguf.md`

```markdown
# SPIKE-01: Adopt `Serveurperso/OmniVoice-GGUF` as hardware-adaptive default cloning engine

**Status:** Proposed (research-supported) — awaiting Phase 2 SubprocessBackend merge
**Date:** 2026-05-18
**Decision-makers:** [maintainer]
**Related:** ROADMAP Phase 4; REQUIREMENTS GGUF-01..06; this Phase 4 RESEARCH.md

## Context

VoiceStudio v0.2.7 ships `k2-fsa/OmniVoice` (separate code/weight terms; 0.6B Qwen3 backbone, Higgs Audio v2 codec) as its default voice-cloning engine via `backend/services/tts_backend.py:VoiceStudioBackend`. The Python in-process path requires PyTorch + CUDA / MPS / CPU.

`Serveurperso/OmniVoice-GGUF` publishes 4 quantizations of the same model (Q4_K_M / Q8_0 / BF16 / F32) consumable through the MIT-licensed `omnivoice.cpp` runtime (a custom GGML-based C++ inference binary). This decision is whether to integrate the GGUF engine as a hardware-adaptive default with overridable fallback.

## Decision

**Technical GO only** — integrate per GGUF-01..06; engine acceptance remains conditional on the rights gate in `docs/engine-acceptance.md`.

## Consequences

**Positive:**
- 4 GB-VRAM GPUs (currently falling back to CPU on the in-process path) get GPU-backed cloning via Q4_K_M.
- Smaller VRAM footprint = stays out of the way of other engines when users run multiple in one session.
- Model licensing needs separate review from the runtime licence; see the correction above.

**Negative / risk:**
- Adds a maintained-by-others C++ runtime to the dependency graph (`omnivoice.cpp`, 38 stars at decision time).
- Adds 12-16 MB of platform binaries to the installer.
- macOS code signing scope expands by 4 binaries (track via REL-05).

**Mitigations:**
- Pin `omnivoice.cpp` by commit SHA; rebuild from pinned SHA in CI.
- In-process `VoiceStudioBackend` remains and is the fallback if any GGUF step fails.
- macOS Apple Silicon Metal build is verified in Wave 1; if blocked, downgrade SPIKE-01 default on macOS to in-process path.

## Sources

- `.planning/phases/04-adaptive-specialty-engines-spike-first/04-RESEARCH.md` (this research)
- https://huggingface.co/Serveurperso/OmniVoice-GGUF (verified 2026-05-18)
- https://github.com/ServeurpersoCom/omnivoice.cpp (verified 2026-05-18)
- https://huggingface.co/k2-fsa/OmniVoice (upstream; separate code/weight terms)
```

### `.planning/decisions/SPIKE-02-singing.md`

```markdown
# SPIKE-02: Adopt `ModelsLab/omnivoice-singing` as singing variant of the existing engine

**Status:** Proposed (research-supported) — awaiting Phase 2 SubprocessBackend merge
**Date:** 2026-05-18
**Decision-makers:** [maintainer]
**Related:** ROADMAP Phase 4; REQUIREMENTS SING-01..05; this Phase 4 RESEARCH.md

## Context

`ModelsLab/omnivoice-singing` is a finetune of `k2-fsa/OmniVoice` (commercial rights unresolved; same Qwen3-0.6B backbone, same Higgs Audio v2 codec, same `omnivoice` PyPI library) trained on additional singing + emotion-tagged data. Activated by a `[singing]` text control tag at generation time.

VoiceStudio's dubbing pipeline currently routes vocal stems (via Demucs) through the default TTS engine, which produces speech output even on sung source material. This decision is whether to integrate the singing finetune as a routed alternative for sung segments.

## Decision

**GO with reduced scope** — integrate per SING-01..05, with SING-02 scope-decided after a Wave 2 read of `dub_pipeline.py`.

## Consequences

**Positive:**
- Sung segments of dubbed content produce sung output (currently produces unsuitable speech-like output).
- Zero new Python deps — same `omnivoice` library already shipping.
- ≤30-line backend subclass.

**Negative / risk:**
- Heuristic segmentation (SING-03 pitch-stability + energy) is approximate; user override per segment is the safety net.
- Cross-language singing quality is acknowledged by the model card as variable.

**Mitigations:**
- Per-segment override available in dubbing UI before commit.
- SING-05 acceptance scoped to native-language singing pass; cross-language flagged as best-effort.
- Model-based classifier deferred to v2 per REQUIREMENTS.md.

## Sources

- `.planning/phases/04-adaptive-specialty-engines-spike-first/04-RESEARCH.md` (this research)
- https://huggingface.co/ModelsLab/omnivoice-singing (verified 2026-05-18)
- https://huggingface.co/k2-fsa/OmniVoice (upstream)
- https://pypi.org/project/omnivoice/ (0.1.5, 2026-04-28)
```

---

## Phase 2 Dependency

Phase 4 is **gated** on Phase 2's `SubprocessBackend` primitive landing. Specifically Phase 4 PLAN.md relies on Phase 2 RESEARCH.md confirming:

- `SubprocessBackend.run(args, stdin, timeout)` can pass CLI args + stdin to a child process (for GGUF binary invocation per Pattern 2 above).
- `mp.get_context("spawn")` IPC is verified on macOS Apple Silicon (per SUMMARY.md Phase 2 open question).
- Per-engine venv bootstrap inherits `HF_HOME` from the parent (per ENGINE-02), so quant downloads land in the existing `$HF_HUB_CACHE`.
- The `is_available()` wrap (ENGINE-05) means a broken `omnivoice-tts` binary on one platform doesn't prevent app boot on others.

**What Phase 4 can do before Phase 2 RESEARCH lands:**
- Web-fetch model cards (this RESEARCH.md).
- Draft `.planning/decisions/SPIKE-01-gguf.md` and `SPIKE-02-singing.md`.
- Build `omnivoice-tts` binaries in CI for the four target platforms (the build itself is independent of Phase 2).

**What Phase 4 cannot do until Phase 2 RESEARCH lands:**
- PLAN.md task wording that references `SubprocessBackend` internals (e.g., specific IPC API, log redaction filter location, venv-bootstrap helper signature).

This is a planner-gate, not a research-blocker. Research completes here; PLAN.md awaits Phase 2 research.

---

## Sources

### Primary (HIGH confidence — verified live 2026-05-18)
- https://huggingface.co/Serveurperso/OmniVoice-GGUF — model card, quant table, downloads, lineage
- https://huggingface.co/ModelsLab/omnivoice-singing — model card, license, usage examples, training data
- https://huggingface.co/k2-fsa/OmniVoice — upstream model card, license, safety language, arXiv 2604.00688
- https://github.com/ServeurpersoCom/omnivoice.cpp — runtime repo, build scripts, CLI args, license
- https://pypi.org/project/omnivoice/ — PyPI 0.1.5, Apache-2.0, 2026-04-28
- `backend/services/tts_backend.py` — existing VoiceStudioBackend pattern (read 2026-05-18)
- `.planning/REQUIREMENTS.md` — SPIKE-01, SPIKE-02, GGUF-01..06, SING-01..05 (read 2026-05-18)
- `.planning/ROADMAP.md` — Phase 4 success criteria (read 2026-05-18)
- `.planning/research/SUMMARY.md` — Phase 2 SubprocessBackend dependency (read 2026-05-18)
- `.planning/research/STACK.md` — context on existing engines (`onnxruntime`, `huggingface_hub`, `transformers`, `supertonic`) (read 2026-05-18)

### Secondary (MEDIUM confidence)
- `omnivoice.cpp` README — Metal build path absence (verified content, not absence-of-evidence — but absence is hard to verify negative)
- Q-variant VRAM sizes from model card (cited but not independently load-tested in this research)

### Tertiary (LOW confidence — flagged for Wave 1 validation)
- Quant quality thresholds in `quant_map.json` defaults — no public benchmark; thresholds informed by general GGUF quant quality knowledge and require Wave 1 head-to-head testing
- SING-03 heuristic accuracy — feasible from existing toolkit but unbenchmarked on VoiceStudio's specific dubbing corpus

---

## Metadata

**Confidence breakdown:**
- Spike artifact identity & licensing: HIGH — model cards verified live, lineage chain traced end-to-end
- Integration shape (SubprocessBackend + subclass pattern): HIGH — matches existing project patterns and Phase 2 contract
- Cross-platform support: HIGH for Linux/Windows/CUDA + CPU; MEDIUM for macOS Apple Silicon Metal (Assumption A1)
- Performance / latency: LOW — no public benchmarks; must validate in Wave 1
- SING-03 heuristic quality: MEDIUM — toolkit available, accuracy unbenchmarked
- Quant-quality threshold defaults: MEDIUM — informed estimates, Wave 1 to validate

**Research date:** 2026-05-18
**Valid until:** 2026-06-17 (30 days; revalidate model card commit SHAs and `omnivoice.cpp` head before Phase 4 PR opens if revisited later)
