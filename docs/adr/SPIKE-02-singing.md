> **Archival note (2026-07-12):** moved here from `.planning/` in the root cleanup.
> Internal `.planning/` / `specs/` paths below are historical — those trees were removed; see git history.

# SPIKE-02: Adopt `ModelsLab/omnivoice-singing` as singing variant of the existing engine

> **Licence correction (2026-10-03):** the original commercial-compatibility
> conclusion is withdrawn. See the [source review](SPIKE-01-gguf-research.md#licence-correction-2026-10-03)
> for separate code, weight, and downstream terms. This engineering decision
> does not approve the model for Pro.

**Status:** ⚠️ **SUPERSEDED (2026-06-14)** by `specs/006-dubbing-singing-mode/` (spec tree removed 2026-07-12 — feature shipped; see git history)
**Date:** 2026-05-18 (superseded 2026-06-14)
**Decision-makers:** [maintainer]
**Related:** ROADMAP Phase 4; REQUIREMENTS SING-01..05; `.planning/phases/04-adaptive-specialty-engines-spike-first/04-RESEARCH.md`

> **Superseded:** This chose `ModelsLab/omnivoice-singing` for singing, but that
> model has **no melody (F0/MIDI) conditioning** — it sings its own melody and
> cannot follow the *source song* a dub must preserve. SoulX-Singer (arXiv
> 2602.07803, published after this decision) provides F0/MIDI conditioning and is
> selected in plan-06. This ADR stays valid only if reframed as an
> expressive-TTS styling toggle, not melody-matched dubbing.

## Context

`ModelsLab/omnivoice-singing` (HuggingFace, 1,053 downloads/month, verified 2026-05-18) is a finetune of `k2-fsa/OmniVoice` — unresolved commercial rights, same Qwen3-0.6B backbone, same Higgs Audio v2 codec at 24 kHz mono, same `omnivoice` PyPI library (0.1.5, 2026-04-28) already shipping in VoiceStudio v0.2.7. Trained on additional singing + emotion-tagged data and activated by a `[singing]` text control tag at generation time.

VoiceStudio's existing `dub_pipeline.py` runs Demucs to split source audio into vocal and instrumental stems and routes the vocal stem through the default TTS engine. Today this produces speech-like output even on sung source material, which is one of the loudest user complaints when dubbing music-adjacent content.

This decision is whether to integrate the singing finetune as a routed alternative for sung segments, with auto-detection + per-segment override.

## Decision

**GO with reduced scope** — integrate per SING-01..05.

The integration shape is `VoiceStudioSingingBackend(VoiceStudioBackend)` — a ≤30-line subclass overriding `id`, `display_name`, the `from_pretrained` model ID, and auto-injecting the `[singing]` control tag in `generate()` unless the prompt already starts with a `[`-prefixed tag. The dubbing pipeline gains a "singing mode" toggle and a segment-routing path (vocal stem → singing engine for sung segments, vocal stem → default engine for spoken segments, instrumental stem preserved untouched). Segment detection uses a pitch-stability + energy heuristic on the Demucs vocal stem with per-segment user override in the dubbing UI.

SING-02's full per-segment routing depth is **decided after a Wave 2 code-read of `dub_pipeline.py`**: if the existing pipeline supports per-segment routing in ≤50 lines, ship it; if it would require >500 lines of refactor, descope to "singing mode applies to entire dubbing job" for v0.3 and defer per-segment to v0.4.

## Consequences

**Positive:**
- Sung segments of dubbed content produce sung output (currently produces unsuitable speech-like output).
- Zero new Python dependencies — same `omnivoice` library already shipping.
- ≤30-line backend subclass; no new engine architecture.
- Hardware footprint identical to existing `VoiceStudioBackend`; runs anywhere the default engine already runs.

**Negative / risk:**
- Heuristic segmentation (pitch-stability + energy) is one-dimensional and misclassifies operatic / sustained-vowel speech and vibrato-heavy speech.
- Cross-language singing quality is acknowledged by the model card as "extrapolation with variable quality."
- `omnivoice-singing` returns garbled output if the `[singing]` tag is missing — automatic injection is load-bearing.

**Mitigations:**
- Per-segment override available in the dubbing UI before any segment is committed to a render (user owns the final route — SING-03 already requires this).
- SING-05 acceptance scoped to native-language singing pass; cross-language flagged as best-effort with model-card disclaimer surfaced in the engine card UI.
- `VoiceStudioSingingBackend.generate()` always prepends `[singing]` unless the prompt already starts with `[`, allowing power users to compose `[singing] [happy]` etc. manually.
- Model-based singing-vs-speech classifier explicitly deferred to v2 per REQUIREMENTS.md Out of Scope.
- License + model-card link surfaced in the engine card UI; first-use acceptance gates download (SING-04).

## Sources

- `.planning/phases/04-adaptive-specialty-engines-spike-first/04-RESEARCH.md` (this milestone's research)
- https://huggingface.co/ModelsLab/omnivoice-singing (verified 2026-05-18)
- https://huggingface.co/k2-fsa/OmniVoice (upstream)
- https://pypi.org/project/omnivoice/ (0.1.5, 2026-04-28)
- `backend/services/tts_backend.py` (existing `VoiceStudioBackend` reference)
- `backend/services/dub_pipeline.py` (existing dubbing pipeline — Wave 2 code-read target)
