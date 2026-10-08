# Competitive Analysis — voicebox · pyvideotrans · Patter (+ second-tier landscape)

> **Dated analysis.** `scripts/validate-install-docs.py`, cited below as an existing
> gate, has since been removed; `scripts/check-docs-drift.py` (`docs-drift.yml`) remains.

*Compiled 2026-06-11 from four parallel research passes (one per competitor repo + a
full feature-surface inventory of this codebase). Star counts and versions are
as-of-date snapshots. VoiceStudio grades (A–D) come from the self-inventory: code
signals, test coverage, TODO density, and open-issue mentions — not marketing.*

*Expanded 2026-06-11 (second pass, six additional research agents): second-tier
landscape survey, source-level deep dives into the flagged components of all three
competitors (clean-room functional specs for the GPL one), implementation specs
mapping every ranked action onto this codebase, and user-sentiment / market-positioning
research. Three grades in the matrix were corrected where the original self-inventory
was stale — see the matrix legend.*

*Expanded again 2026-06-12 (third pass): the
[Roadmap directions](#roadmap-directions-community-discussion-346) section grounds
the feature roadmap announced in discussion #346 — agentic voice workflows, remote
GPU backends, audiobook creator, persona gallery, model/env + GPU-compat management —
in landscape, license, and regulatory research, with scope ladders and eight new
consolidated actions (15–22).*

## TL;DR

| | [voicebox](https://github.com/jamiepine/voicebox) | [pyvideotrans](https://github.com/jianchang512/pyvideotrans) | [Patter](https://github.com/PatterAI/Patter) |
|---|---|---|---|
| What | Local-first voice studio (ElevenLabs + WisprFlow alt) — **our most direct competitor** | Desktop video translate/dub pipeline (GUI + CLI) | Telephony voice-agent SDK (self-hosted Vapi/Retell alt) — adjacent, not competing |
| Stack | Tauri v2 + React + FastAPI (same as us) | PySide6 (Qt) + FFmpeg, 100 % Python | Python + TS dual SDK |
| Maturity | v0.5.0, ~29.7k★, fast cadence, beta-grade hardware backlog | V4.01, ~17.9k★, 2.5 yrs mature, monthly releases | v0.6.x, ~511★, 2 months old, exceptionally well-engineered |
| License | **MIT** | **GPL-3.0** | **MIT** |
| Code reuse for us | ✅ **Port directly** (keep MIT attribution header) | ⚠️ **Reimplement ideas only — never copy** | ✅ **Port directly** (keep MIT attribution header) |

**Second-pass promotion:** [KrillinAI / KlicStudio](https://github.com/krillinai/KlicStudio)
(Go, ~10.3k★, GPL-3.0, v2.0.3 released 2026-06-09, only 28 open issues) is now rated
**as direct a competitor as voicebox for the dubbing user** — desktop+web+CLI form
factors and a deliberate "for AI agents" strategy that competes with our MCP angle.
GPL-3.0 → study-only. Full profile in the [second-tier landscape](#second-tier-landscape)
below; it has not been folded into the per-capability matrix because we have not done
a capability-by-capability source pass on it yet.

**License ground rule.** VoiceStudio is **AGPL-3.0-only with a commercial
dual-license offering**. MIT code can be incorporated (attribution preserved) and
stays compatible with selling commercial exceptions. GPL-3.0 code is technically
combinable with AGPL-3.0 (GPLv3 §13), **but** copied GPL files stay GPL-3.0 forever
under the original author's copyright — which would break the commercial-license
model (we can only sell exceptions for code we own). So pyvideotrans is a
*design-document*, not a code source: study `_rate.py`, write our own.
The same logic applies to **third-party AGPL code** (e.g. alltalk_tts): even though
we are AGPL ourselves, we cannot sublicense someone else's AGPL code under our
commercial exception — study-only, same as GPL.

Fun fact discovered en route: **pyvideotrans already integrates VoiceStudio as
a first-class TTS/clone backend** (`videotrans/tts/_omnivoice.py`, via our Gradio
API). We are upstream for 17.9k-star project users. **Second-pass update: the
integration is verified broken** — it speaks Gradio to an endpoint we never exposed.
Details in the [pyvideotrans deep dive](#7-fact-check-the-pyvideotrans--omnivoice-integration);
the action item is now "ship a compat shim or upstream a REST integration", not
"verify".

---

## Big feature matrix

Legend — **Us**: A–D maturity grade from the self-inventory. **Them**: ✅ stable ·
🟡 beta/partial · ❌ absent. *(bold = they beat us; this is the gap list)*
*Second-pass corrections (marked △): three "Us" cells in the original were stale —
the self-inventory missed `scripts/validate-install-docs.py`, the probe-judge +
`omnivoice/eval/` stack, and the real FastMCP server at `backend/mcp_server.py`.*

| Capability | Us | voicebox | pyvideotrans | Patter | Notes |
|---|---|---|---|---|---|
| **Generation & cloning** |
| Zero-shot voice cloning | B | ✅ | ✅ (via clone-TTS engines) | ❌ | Parity; their multi-sample profiles are slightly ahead |
| Preset voice library (no reference audio) | B+ (20+ archetypes) | ✅ **50+ presets** (Kokoro/Qwen) | ❌ | ❌ | They win on count, we win on curation + degenerate-check |
| Voice design from text description | B | 🟡 (personality descriptors) | ❌ | ❌ | We're ahead (#317 shipped a deterministic mapper) |
| **Unlimited-length generation (chunk + crossfade)** | ❌ (no auto-chunking) | ✅ `chunked_tts.py` | ✅ (per-subtitle by design) | ❌ | **Gap.** Their crossfade chunker removes the length ceiling |
| Paralinguistic tags (`[laugh]`, `[sigh]`) | B (13 native reaction tags via ⊕ Insert; no `[breath]` yet) | ✅ (Chatterbox Turbo) | ❌ | ❌ | Near-parity — see docs/expressive-speech.md; CosyVoice 3 adds `[breath]`/`[laughter]` |
| Delivery instructions ("whisper", "slowly") | B (instruct field) | ✅ (Qwen NL control) | ❌ | ❌ | Parity-ish |
| Generation queue w/ cancel + SSE | B+ (job store, SSE replay) | ✅ | ✅ (9-queue pipeline) | n/a | Parity; our SSE reconnect-replay is ahead of voicebox |
| Post-processing FX chain (reverb/pitch/comp) | B (effect chain exists) | ✅ **Pedalboard, per-profile presets** | ❌ | ❌ | Theirs is richer + has preset UX |
| Multi-track timeline editor (stories/podcasts) | ❌ | ✅ **Stories editor (v0.5.0)** | ❌ | ❌ | **Gap** — also the #280-item-3 timeline ask |
| Audio watermarking (AudioSeal) | B | ❌ | ❌ | ❌ | **We're unique here** |
| **Dubbing pipeline** |
| Full video dub (ASR→translate→TTS→mux) | A– | ❌ | ✅ (1200-line battle-tested pipeline) | ❌ | Two-horse race; we're competitive |
| Incremental re-dub (change 1 line, regen 1 segment) | A– (#281 fixed) | ❌ | ❌ | ❌ | **We're unique here** |
| **Dub-length fitting (audio speedup + video slowdown)** | A– (Smart Fit complete: planner + generate path + fit fingerprints + two-tier video-retime export with drift absorption and fitted subtitles) | ❌ | ✅ **`_rate.py` — the crown jewel** | ❌ | **Gap closed** — Action 1 reimplemented clean-room (`services/fit_planner.py`, `services/video_retime.py`, [Spec 1](#spec-1--dub-length-fitting-v2)) |
| Vocal/BGM separation + re-mix | A– (Demucs 4-stem) | ❌ | ✅ (UVR/Spleeter ONNX) | ❌ | Parity; their ONNX models are lighter than Demucs |
| **Clone refs cut from separated vocals per segment** | 🟡 (speaker_clone refs 5–15 s/speaker) | ❌ | ✅ per-subtitle-line refs | ❌ | Their per-line granularity beats our per-speaker. Action 4 |
| Speaker diarization → multi-voice dub | B+ (pyannote) | ❌ | ✅ (4 backends incl. CAM++) | ❌ | Parity; their backend choice is wider |
| **Second-pass ASR on dubbed audio** (regenerate exact subtitle timings) | ❌ | ❌ | ✅ | ❌ | **Gap** — clever QC step. Action 5 |
| Subtitle styling / burn-in / dual-language | A– (#309 fixed) | ❌ | ✅ | ❌ | Parity |
| Batch processing (N videos) | B (50-job queue) | ❌ | ✅ (wave control, multi-GPU scaling) | ❌ | Their `batch_nums` waves + per-GPU thread scaling is ahead |
| Translation channel breadth | B (LLM 3-step chain + glossary) | ❌ | ✅ **~25 channels** | ❌ | Breadth vs depth: our reflect/adapt chain is deeper, their coverage wider |
| Translation caching + line-count validation | 🟡 (fingerprints #281) | ❌ | ✅ MD5 cache + timeline re-match | ❌ | Worth studying |
| **Dictation** |
| Global-hotkey dictation pill | B+ (#323 fixed) | ✅ (v0.5.0, auto-paste **macOS-only**) | ❌ | ❌ | We're ahead on cross-platform (their gap violates our parity rule) |
| **LLM transcript refinement (filler-word removal)** | ❌ | ✅ local Qwen3 0.6B–4B | ❌ | ❌ | **Gap.** Action 3 |
| **Captures library (replay / re-transcribe / refine)** | 🟡 (transcription history page) | ✅ richer (v0.5.0) | ❌ | ❌ | Partial gap — we store, they iterate |
| Dictation while audio plays (echo cancel) | ✅ opt-in (Settings → Capture): server-side NLMS AEC on `/ws/transcribe?aec=1` + AudioWorklet PCM mic + player far-end tap — Action 8 | ❌ | ❌ | ✅ NLMS AEC | Ported Patter's NLMS canceller. Action 8 |
| **Engines & platform** |
| TTS engine count | B (6) | ✅ 7 | ✅ **33 channels** (22 ASR, 25 translate) | ✅ 7 (cloud) | pyvideotrans = breadth king (incl. cloud); we + voicebox are local-only by design |
| Engine plugin protocol | B+ (ABC + registry) | ✅ Protocol + ModelConfig registry, **agent skill for adding engines** | ✅ lazy dataclass plugins | ✅ provider SDK | Everyone converged on the same pattern; their `requires_cuda`-gap lesson is free for us |
| **MLX runtime on Apple Silicon** | 🟡 (MLX-Audio engine only) | ✅ **MLX for TTS+STT, 4–5× claimed** | ❌ | ❌ | **Gap** — dual-runtime per engine. Action 6 |
| **CUDA binary auto-download (small installer)** | ❌ (venv on first run ships everything) | ✅ in-app CUDA swap incl. sm_120 | ❌ | ❌ | Different bootstrap philosophy; their #1 bug source too. Study only — failure-mode autopsy in the deep dive |
| Crash-isolated engine subprocesses | 🟡 (Demucs/ffmpeg subprocesses) | ❌ | ✅ (whisper.cpp etc. in child procs) | ❌ | Their JSON-log polling pattern is a cheap stability win. Action 7 |
| ROCm support | A– (with edge cases) | 🟡 (large breakage backlog) | 🟡 | n/a | We're ahead |
| **Integration surface** |
| OpenAI-compatible API | B+ | ✅ REST | ❌ | n/a | Parity |
| **MCP server (agent speaks in your voice)** | △ B– (FastMCP `backend/mcp_server.py`: 4 tools, stdio + SSE; **not mounted on the main app, no per-agent voice binding**) | ✅ **FastMCP at `/mcp` + stdio shim, per-agent voice binding** | ❌ | ✅ (client + server) | Gap is narrower than originally graded; what's missing is exactly the half voicebox shipped. Action 2 |
| Web/Docker deployment | B– (headless image exists) | ✅ (`docker compose up`) | ❌ (desktop only) | ✅ | Parity-ish; our :latest/:stable retag (PR #338) helps |
| CLI / headless batch | 🟡 (API only) | ❌ | ✅ `cli.py` (stt/tts/sts/vtv) | ✅ | Partial gap for power users |
| Streaming TTS (websocket, low TTFA) | C+ (`/ws/tts` experimental) | ❌ | ❌ | ✅ **sentence-chunked streaming, first-flush** | Patter's chunker + first-flush are portable. Action 8 |
| **Ops & quality discipline** |
| Eval harness for output quality | △ 🟡 (probe judges `tests/probe/judges/` + `omnivoice/eval/` WER/MOS/speaker-sim — deterministic tier exists, **no semantic/LLM-judge tier**) | ❌ | ❌ | ✅ LLM-judge evals + CLI | Patter's harness adds the missing *semantic* tier. Action 9 |
| **Docs-drift CI** | △ 🟡 (`scripts/validate-install-docs.py` gates `docs/install/*.md` in ci.yml — **no inventory-wide drift job**) | ❌ | ❌ | ✅ daily inventory-vs-docs diff job | Patter's rolling-issue automation is the missing half. Action 9 |
| Model-evaluation decision log | 🟡 (ROADMAP phases) | ✅ `PROJECT_STATUS.md` accepted/abandoned log | ❌ | ❌ | Cheap practice to adopt |
| Telemetry design (consent-bounded) | n/a (opt-in GH Issues only) | ❌ | ❌ | ✅ consent module, bucketed values | Reference design for the bug reporter — allowlist pattern in the deep dive |

### Where we are unique (defend these)

- **Incremental re-dub** with fingerprint tracking — nobody else has it.
- **646-language claim** via VoiceStudio model — voicebox tops out at 23, pyvideotrans is engine-dependent.
- **AudioSeal watermarking + detection** — unique among all three.
- **Cross-platform dictation as a default** (their auto-paste is macOS-only).
- **3-step LLM translation chain (translate → reflect → adapt) + glossary** — deeper than anyone's single-pass.

*Second-pass reality check: market evidence per item — which of these users actually
ask for — is in [User sentiment & positioning](#honest-verdicts-on-our-unique-five).
Short version: (a) and (d) are strong levers, (b) is a reach lever with a quality-risk
tail, (c) and (e) are real but nobody searches for them by name.*

---

## Second-tier landscape

*Surveyed 2026-06-11; stars / last-push / open-issue counts verified via the GitHub
API. These either compete for the same user or carry portable ideas, but none (except
KrillinAI) warrants a per-capability matrix column yet.*

| Project | What | Stack | Maturity (2026-06-11) | License | Overlap w/ us | Verdict |
|---|---|---|---|---|---|---|
| [KrillinAI/KlicStudio](https://github.com/krillinai/KlicStudio) | LLM video translate+dub for humans *and AI agents* | **Go** + web/desktop/CLI | 10.3k★, v2.0.3 **2026-06-09**, 28 issues | GPL-3.0 | Dubbing, cloning, API server | **Direct competitor (promoted)**; reimplement only |
| [VideoLingo](https://github.com/Huanshere/VideoLingo) | One-click "Netflix-grade" subtitle + dubbing pipeline | Python/Streamlit, cloud LLM | 17.4k★, v3.0.1 2026-02, 208 issues | Apache-2.0 | Dubbing, cloning (GPT-SoVITS), subtitles | Direct competitor (web, cloud-LLM-dependent); **port OK** |
| [voice-pro](https://github.com/abus-aikorea/voice-pro) | All-in-one local cloning/TTS/Whisper/Demucs/translate WebUI | Python/Gradio (Windows-leaning) | 10.9k★, push 2025-12, 47 issues | GPL-3.0 | Cloning, TTS, STT, separation, translation | Direct competitor (web form factor, 6 months quiet); **reimplement only** |
| [ebook2audiobook](https://github.com/DrewThomasson/ebook2audiobook) | Ebook → audiobook w/ cloning, 1,158+ languages | Python/Gradio + CLI/Docker | 19.2k★, push **2026-06-11**, **10 issues** | Apache-2.0 | Cloning, multi-engine TTS | Adjacent vertical; **port OK** |
| [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) | Few-shot cloning + TTS WebUI w/ training tools | Python/Gradio + API | 58.6k★, push 2026-04, 872 issues | MIT | Cloning, TTS (we already wrap it as `GPTSoVITSBackend`) | Component + partial competitor; **port OK** |
| [SoniTranslate](https://github.com/r3gm/SoniTranslate) | Gradio video-dubbing pipeline w/ diarization + cloning | Python/Gradio | 1.4k★, push 2026-04, **no release since 2024-05**, 122 issues | Apache-2.0 | Dubbing, cloning, multi-TTS (we sidecar it) | Component + adjacent; **port-with-attribution OK** |
| [Speaches](https://github.com/speaches-ai/speaches) | OpenAI-compatible local STT+TTS+Realtime server | Python/Docker | 3.4k★, push 2026-06-10, 136 issues | MIT | API-server surface only | Component-not-competitor; **port OK** |

### Notable ideas / gaps per project

**KrillinAI / KlicStudio** — the promotion case: released two days before this survey,
28 open issues at 10.3k★ (most operationally disciplined direct competitor), and a
`skills/` Agent-Skills framework exposing stable CLI contracts + JSON artifact
manifests so AI agents drive each pipeline stage independently — it validates and
extends our MCP-server direction. Also: portrait/landscape re-rendering + cover
generation for TikTok/Shorts, a social-publishing tail we ignore. GPL-3.0: ideas only.

**VideoLingo** — its "Translate-Reflect-Adaptation" chain is the same 3-step idea as
our `translator.py` chain (convergent evolution; theirs predates the comparison —
worth a diff of prompt strategies, Apache-2.0 so even portable). Netflix-standard
subtitle segmentation with word-level alignment overlaps our `subtitle_segmenter.py`.
Per-step pause/resume/stop on long pipelines is a UX gap we have. Counter-positioning:
it *requires* a cloud LLM key — our local-first translation is the differentiator to
message against it.

**voice-pro** — closest single-app feature overlap in the tier (cloning + TTS +
Whisper + Demucs + YouTube ingest + translation). Proves 10k+★ demand for exactly our
bundle in a clunkier (Gradio, Windows-leaning, GPL) package; quiet since 2025-12 —
its users are capturable if it stalls. The recurring expectation across this whole
tier (voice-pro, VideoLingo, KrillinAI, SoniTranslate): built-in
YouTube-download → process loop. Our URL-ingest dub path covers part of this.

**ebook2audiobook** — the long-form vertical we don't serve: chapterized m4b output,
inline SML tags (`break`/`pause`/voice-switch mid-text), OCR'd PDFs, per-file voice
mapping in batch. A "narrate a whole book" mode is a credible extension (Apache-2.0,
portable). Separately: 10 open issues at 19.2k★ is the best issue-hygiene benchmark
in the landscape — study their template/triage setup for our bug-reporting milestone.

**GPT-SoVITS** — dual role: an engine we wrap *and* a competitor for the DIY cloning
user. The big capability we lack vs. that crowd is its in-app fine-tuning chain
(dataset slicing → ASR → labeling → train); MIT, so the training-tool code is
portable. Version churn (v2/v2Pro/v3/v4) is a live threat to our `GPTSoVITSBackend`
engine-compat constraint — pin and contract-test the API surface.

**SoniTranslate** — we already depend on it: `backend/services/sonitranslate.py` runs
it as an isolated Gradio sidecar (port 7860) via `gradio_client`. Apache-2.0 means we
may also vendor its code in-tree with attribution if sidecar reliability ever becomes
a problem (process-level integration carries no derivative-work questions at all, so
the current setup is the safest). **Risk: its release cadence stalled (v0.5.0,
May 2024)** while we depend on it at runtime — consider pinning a fork.

**Speaches** — "Ollama for audio": dynamic model auto-load/unload per request is
directly applicable to our multi-engine VRAM juggling (MIT, portable). Its OpenAI
Realtime-API emulation is the compatibility surface third-party clients will
eventually ask our API server for. From the same bucket:
[Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) (Apache-2.0, 5.0k★) ships
**weighted voice mixing** (blend voices by ratio) — a cheap, differentiating voice-design
feature. New Action 12.

### Watch items (not competitors)

- **[TTS-WebUI](https://github.com/rsxdalv/TTS-WebUI)** (MIT, 3.2k★, active) — a
  40+-model local audio hub whose repo description **already advertises a VoiceStudio
  extension**. Verify what that extension wraps and that AGPL terms are respected.
  Its per-extension `uv` venv isolation parallels our sidecar approach.
- **[F5-TTS](https://github.com/SWivid/F5-TTS)** (14.7k★) — engine candidate, not an
  app. Trap: code is MIT but **pre-trained weights are CC-BY-NC** — an F5-TTS engine
  would need prominent non-commercial-weights labeling like our existing
  engine-license gates.
- **[alltalk_tts](https://github.com/erew123/alltalk_tts)** (AGPL-3.0, 2.4k★, solo
  maintainer) — adjacent TTS server. Third-party AGPL = study-only for us (see
  license ground rule). Its narrator/character voice-switching markup and low-VRAM
  modes are reimplementable ideas.
- **[Linly-Dubbing](https://github.com/Kedreamix/Linly-Dubbing)** (Apache-2.0, 3.2k★,
  ~15 months stale) — idea quarry only; its lip-sync integration (re-syncing mouth
  movements to the dubbed track) is the one feature nobody in the landscape, us
  included, ships.
- **[resemble-ai/chatterbox](https://github.com/resemble-ai/chatterbox)** (~25.0k★,
  MIT) — engine candidate; full evaluation in the appendix.

---

## Ranked actions

Effort: S < 1 day · M = 1–3 days · L = 1–2 weeks. "Port" = copy + adapt MIT/Apache code
with attribution header. "Reimplement" = clean-room from the functional spec in this
doc — **do not open pyvideotrans source files while writing ours** (the specs below
were written by a dedicated spec pass precisely so implementers never have to).
Each action now links to an implementation spec mapped onto our codebase.

| # | Action | From | Mode | Effort | Why now |
|---|---|---|---|---|---|
| 1 | **Dub-length fitting v2**: absorb inter-segment silence slack → if speedup ≤ 1.2× stretch audio only (pitch-preserving) → else split burden ~50/50 with per-segment video slowdown → regenerate subtitle timeline from actual dub durations → freeze last frame for drift | pyvideotrans `_rate.py` design | **Reimplement** ([spec](#spec-1--dub-length-fitting-v2)) | L | Our #280 onset-snapping is a band-aid; this is the algorithm that makes dubs *fit*. Highest user-visible quality win available |
| 2 | **MCP server v1**: mount the existing FastMCP server on the main FastAPI app + stdio shim + per-agent voice binding | voicebox `backend/mcp_server/`, `mcp_shim/` | **Port** ([spec](#spec-2--mcp-server-v1)) | M | We already have the FastMCP half (`backend/mcp_server.py`); voicebox shipped exactly the missing half. Agents-speak-in-your-voice is organic marketing |
| 3 | **Dictation refinement**: deterministic repetition-collapse pre-pass + optional local-LLM filler-word removal before paste | voicebox `services/refinement.py` | **Port** (adapt to our `llm_backend.py`) ([spec](#spec-3--dictation-refinement)) | M | Biggest dictation quality jump per line of code; WisprFlow's whole pitch. The deterministic pre-pass alone (fixes Whisper hallucination loops) is worth shipping first |
| 4 | **Per-segment clone refs**: cut the voice-clone reference for each dub segment from the separated vocal track at that segment's timestamps, instead of one 5–15 s ref per speaker | pyvideotrans per-line ref idea | **Reimplement** ([spec](#spec-4--per-segment-clone-refs)) | S–M | Prosody of each line matches its source line; cheap because Demucs stems + segment times already exist |
| 5 | **Second-pass ASR QC**: after dub generation, re-run ASR on the synthetic audio to regenerate exactly-timed subtitles (and flag segments whose recognized text drifts from the target text) | pyvideotrans pipeline stage | **Reimplement** ([spec](#spec-5--second-pass-asr-qc)) | M | Turns subtitle timing from "trusted math" into "measured truth"; doubles as an automatic dub-quality check |
| 6 | **MLX runtime pass**: route Whisper + at least one TTS engine through MLX on Apple Silicon via backend-aware model-repo mapping | voicebox `mlx_backend.py` pattern | **Port** pattern ([spec](#spec-6--mlx-runtime-pass)) | L | M-series Macs are a huge slice of local-AI users; 4–5× claimed speedup |
| 7 | **Crash-isolated ASR subprocess**: run native-crashy engines (whisper.cpp class) in a single-use child process, so a segfault never kills the backend | pyvideotrans subprocess pattern | **Reimplement** ([spec](#spec-7--crash-isolated-asr)) | M | Directly serves "first-run that actually works"; engine crashes become per-job failures. The sentiment pass confirmed whisper GPU-teardown crashes are endemic to the category |
| 8 | **Streaming polish kit**: sentence-aware chunker (abbreviation + multilingual punctuation, aggressive first-flush for TTFA) for `/ws/tts` + NLMS AEC so dictation works during playback | Patter `sentence_chunker.py`, `aec.py` | **Port** ([spec](#spec-8--streaming-polish-kit)) | M | Moves `/ws/tts` from C+ experiment toward production; AEC unlocks dictate-over-playback |
| 9 | **Quality rails**: (a) extend our existing install-docs gate into a daily inventory-vs-docs drift job with rolling-issue automation; (b) LLM-judge eval harness as a *non-gating* semantic tier above our deterministic probe judges | Patter `docs-feature-drift.yml`, `evals/` | **Port** ([spec](#spec-9--quality-rails)) | S + M | (a) we already have `validate-install-docs.py` — this is the second half; (b) gives dub translation quality what the probe judges give DSP correctness |
| 10 | **Practice adoptions** (no code): `PROJECT_STATUS.md`-style engine decision log; platform-gating flag audit in our engine registry (pre-empt voicebox's top bug class); add Chatterbox Turbo to the engine roster for paralinguistic tags | voicebox | n/a | S each | Cheap, compounding |
| 11 | **Fix the pyvideotrans bridge**: their `_omnivoice.py` integration is **verified broken** (speaks Gradio `/_clone_fn` to our FastAPI :3900 — hard-stops on connect). Either ship a small Gradio-compatible shim or upstream a REST/OpenAI-style integration PR to pyvideotrans | pyvideotrans | **Build/PR** ([spec](#spec-11--pyvideotrans-bridge)) | S–M | 17.9k★ project routing users to us — the bridge is down right now |
| 12 | **Weighted voice mixing**: blend preset/cloned voices by ratio as a voice-design primitive | Kokoro-FastAPI (Apache-2.0) | **Port** | S–M | Cheap, differentiating, and feeds the voice-design surface where we're already ahead |
| 13 | **Dynamic engine load/unload**: per-request model lifecycle (load on demand, unload on idle/VRAM pressure) — 🟡 **idle-unload shipped for both engine classes**: in-process default model via `model_manager.idle_worker`; subprocess-engine sidecars via `subprocess_backend.reap_idle_sidecars` (lock-guarded, `OMNIVOICE_SIDECAR_IDLE_TIMEOUT_S`, default 300 s). Cross-engine VRAM-pressure preemption (unload B to make room for A) still TODO | Speaches (MIT) | **Port** pattern | M | Multi-engine VRAM juggling is our chronic background pain; "Ollama for audio" solved it |
| 14 | **Positioning kit** (no code): ElevenLabs-dubbing-cost comparison page + incremental-re-dub demo GIF; Show HN; name-collision disambiguation; "WisprFlow alternative" docs entry point | sentiment research | n/a | S each | Grounded in what users actually search; details in [positioning moves](#positioning-moves) |

### Explicitly not recommended

- **Copying any pyvideotrans / KrillinAI / voice-pro code** — GPL-3.0 files would stay
  GPL under their author's copyright inside our AGPL tree and break the commercial
  dual-license. Ideas are fair game; code is not. Same for third-party **AGPL** code
  (alltalk_tts).
- **Cloud TTS/ASR/translate channel breadth** (pyvideotrans's 33/22/25) — violates
  the local-first constraint. Our breadth play is local engines only.
- **voicebox's CUDA-binary-swap bootstrap** — their own top bug category; our uv-venv
  bootstrap is healthier. The deep dive below documents six concrete failure modes to
  avoid; the lessons transfer to our sidecar pattern, the mechanism does not.
- **Patter's telephony stack** — different product. Only the audio/streaming/ops
  pieces above are relevant.
- **Patter's opt-out telemetry default** — its *allowlist/bucketing design* informs
  our opt-in bug reporter (see deep dive), but the consent default and the
  third-party endpoint both violate our local-first constraint.
- **F5-TTS as an unlabeled engine** — MIT code, but CC-BY-NC weights; only with the
  same prominent license gate we use for OpenRAIL engines.

---

## Deep dives

*Source-level briefs from the second research pass. voicebox and Patter are MIT —
briefs may quote and porting means copying with attribution. The pyvideotrans
section is a **clean-room functional spec**: written by a dedicated spec pass that
read the GPL source so implementers never have to; it contains no code, only
behavior and constants. File paths are repo-relative, with approximate line ranges,
for verification only.*

### voicebox (MIT — portable)

#### 1. Chunked long-form TTS (`backend/utils/chunked_tts.py`, 299 LOC)

Engine-agnostic wrapper around any `generate()`. Text ≤ `max_chunk_chars` (default
**800**, per-request overridable) takes a zero-overhead single-shot path. Longer text
splits greedily left-to-right with descending boundary priority: sentence end
(`[.!?]` + CJK equivalents, skipping a 25-entry abbreviation set and decimals) →
clause boundary (`;:,—`) → whitespace → safe hard cut. Paralinguistic tags
(`[laugh]`) are atomic — a regex guards every split candidate. Per-chunk seed is
`seed + i` (decorrelates RNG artifacts, keeps (text, seed) reproducible). Optional
per-chunk `trim_fn` for engines that hallucinate trailing noise. Joining is a linear
crossfade, default **50 ms** (0 = hard cut), overlap clamped to the shorter side.
Known latent bug: sample rate is taken from the *first* chunk; later mismatches are
silently ignored. No per-chunk failure recovery — any chunk exception fails the whole
generation. Deps: numpy only.

**Porting:** new `backend/utils/` module invoked from our `tts_backend.py` synthesis
orchestration — it only needs an awaitable `generate()` returning `(ndarray, sr)`,
which our `TTSBackend` ABC already provides. Make the abbreviation set and
sentence-end regexes language-aware for our 646-language scope; wire
`max_chunk_chars`/`crossfade_ms` into the request models. Not a fit for streaming
`/ws/tts` as-is (batch concat, not incremental emit), but `split_text_into_chunks`
alone is reusable there. **Effort: S.**

#### 2. Dictation LLM refinement (`backend/services/refinement.py`, 295 LOC + capture plumbing)

Local Qwen3 **0.6B/1.7B/4B** (mlx-community 4-bit on Apple Silicon, upstream
elsewhere), default 0.6B, `temperature=0.2`, thinking off. Prompts are assembled from
three boolean toggles (`smart_cleanup`, `self_correction`, `preserve_technical`, all
default on) — no raw prompt editor. The base instruction frames the model as "a text
filter, not an assistant" with explicit anti-instruction-following rules. Seven
few-shot examples are passed as **structured chat turns**, not inline prompt text —
small models pattern-match and echo inline examples; ordering is recency-weighted
with hardest rules last.

The underrated half is a **deterministic pre-pass**, `collapse_repetitive_artifacts()`:
strips Whisper hallucination loops *before* the LLM — word-level (token repeated ≥6×)
and character-level (any 2–60-char unit repeating ≥6×, catches CJK loops with no
spaces). Rhetorical repeats below 6 survive. This alone fixes the classic Whisper
"thanks for watching" loop and works with no LLM configured.

Flow: record → full-audio STT (raw transcript persisted) → if auto-refine, one LLM
round-trip → refined-or-raw text is what auto-pastes. Refinement is re-runnable with
different flags. A readiness endpoint checks *on-disk* model presence so the global
hotkey never hangs on an un-downloaded model; refine errors leave the raw transcript
standing.

**Porting:** maps almost 1:1 onto our dictation stack. Pre-pass + prompt builder →
new `backend/services/refinement.py`; the LLM call goes through our existing
`llm_backend.py` OpenAI-compat adapter (Ollama/LM Studio) instead of an in-process
Qwen — simpler: the few-shot examples become standard `messages` pairs. Hook after
the **final** transcript in `capture_ws.py`, never on partials. Readiness becomes
"is an LLM endpoint configured/reachable". Needs Settings toggles + i18n keys.
**Effort: M** (service S; UX, persistence, ws-protocol addition make it M).

#### 3. MCP server + stdio shim (`backend/mcp_server/` ~650 LOC, shim ~200 LOC)

FastMCP **mounted on the main FastAPI app** at `/mcp` (Streamable HTTP); FastMCP's
session manager must run inside the ASGI lifespan — they stack their existing
startup/shutdown with FastMCP's via `AsyncExitStack`. Tools (dotted names):
`voicebox.speak(text, profile, engine, personality, language)` → async
`{generation_id, poll_url}`; `voicebox.transcribe(audio_base64 | audio_path)` —
`audio_path` **restricted to loopback callers** (so a 0.0.0.0-bound server isn't an
arbitrary-file-read primitive), 200 MB cap; `voicebox.list_captures`,
`voicebox.list_profiles`.

**Per-agent voice binding** — the headline: every MCP client sends an
`X-Voicebox-Client-Id` header (from its MCP config, or forwarded by the shim from an
env var). Middleware copies it into a `ContextVar` so tool handlers read it without
plumbing, and stamps `last_seen_at`. A `mcp_client_bindings` table holds per-client
`{label, profile_id, default_engine, default_personality}` — "Claude Code speaks in
Morgan, Cursor in Scarlett." Resolution precedence: explicit tool arg → per-client
binding → global default → helpful error. Bindings managed from Settings over plain
REST. The stdio shim is a ~200-line stdio↔HTTP proxy (waits for `/health`, relays
JSON-RPC, captures/replays the MCP session id, maps HTTP errors to JSON-RPC errors);
only dep is httpx.

**Porting:** we already have the FastMCP half (`backend/mcp_server.py`, stdio+SSE,
4 tools) — this is precisely the missing half. Order: (1) mount + lifespan
composition on our main app (wrap our startup hooks, don't replace); (2) client-id
middleware + bindings table (alembic migration per the backward-compat constraint) +
resolve chain over our voice profiles; (3) shim nearly verbatim. The loopback gate on
file-path tools is a security pattern worth copying anywhere. **Effort: M.**

#### 4. MLX runtime (`backend/backends/mlx_backend.py`, 367 LOC + factory routing)

Not per-engine plugins — a **fork inside the lazy backend factory**: only Qwen TTS,
Whisper STT, and the Qwen3 LLM branch on `get_backend_type()`; everything else is
torch-only everywhere. Detection: Darwin+arm64, then `import mlx.core` inside
try/except catching `ImportError, OSError, RuntimeError` — in a PyInstaller bundle
the native dylib/metallib can fail to load even when the package imports. The fork
extends to **model selection**: the same `model_size` key resolves to
`mlx-community/...` weights on MLX (~3× smaller downloads) vs upstream weights on
torch. Backends are duck-typed Protocols; inference runs via `asyncio.to_thread`;
clone failure degrades to generation without the voice prompt. Claimed 4–5× speedup
is README-grade (no benchmark file in the repo).

**Porting:** we already have `MLXAudioBackend` in the registry, so the structure
exists. Worth stealing: (a) backend-aware ModelConfig repo mapping — one engine key,
platform-resolved artifact; (b) the robust `import mlx.core` probe (we will hit the
same PyInstaller failure in bundled builds); (c) MLX Whisper for the dictation path —
we already have `MLXWhisperBackend` in `asr_backend.py`, the gap is routing dictation
through it. Cross-platform rule satisfied: MLX is implementation-level, default
behavior identical. **Effort: S–M.**

#### 5. Stories multi-track editor (shape only; `backend/services/stories.py`, 966 LOC)

Two tables: `Story {id, name, description, timestamps}` and `StoryItem {id, story_id,
generation_id FK, version_id FK nullable, start_time_ms, track, trim_start_ms,
trim_end_ms, volume, created_at}`. The key design move: **a clip references a
Generation, never copies audio** — trims are non-destructive offsets, and
`version_id` pins a clip to a specific regeneration take while sibling clips can use
different takes. Split = two items sharing one `generation_id` with complementary
trims (row-locked against double-click races). Export sums clips into a float32
buffer at sample offsets (overlaps mix additively), then peak-normalizes if needed.

**Porting:** the 9-column schema is the valuable part — a full multi-track NLE over
our existing generation history (additive alembic migration, backward-compatible).
The frontend timeline is the actual cost. **Effort: L full feature, S for data model
+ export mixer alone.**

#### 6. CUDA binary swap bootstrap — study-only failure autopsy (`backend/services/cuda.py`, 422 LOC + Rust launch logic)

Mechanism: CPU PyInstaller sidecar ships in the installer; a CUDA variant downloads
as two independently versioned tarballs (server core, versioned with the app; CUDA
libs ~4 GB, keyed on a toolkit string). At every launch the Rust shell runs the
downloaded binary with `--version` and compares; mismatch or error → silent CPU
fallback. Six failure modes documented from their tracker, **all to avoid**:

1. Staleness detection requires *spawning the possibly-broken binary* — corrupt
   onedir → `--version` fails → silently CPU, never repaired this launch. (They also
   paid a 30 s torch import on every version check until adding a fast path.)
2. One-launch GPU lag after every app update ("update disabled my GPU" reports).
3. Extract-over-old-dir without wiping — orphaned files from previous layouts shadow
   the new binary while `--version` still passes.
4. Auto-update vs manual download raced on the same temp file (fixed late with a
   lock that now silently *skips* user-initiated downloads).
5. Manifest-vs-disk drift: libs staleness reads a JSON manifest that survives failed
   or hand-deleted extractions; toolkit bumps force full 4 GB re-downloads.
6. Every failure path degrades to CPU with stdout-only logging — users discover it
   as "the app got slow", not as an error.

**Lessons for our sidecars** (`subprocess_backend.py` + engine bootstraps): write the
version manifest atomically *after* successful extract — never interrogate a binary
to learn its version; extract to temp dir + atomic rename; make GPU/CPU fallback
loudly visible in the UI; the split-archive idea (app-versioned core vs
toolkit-versioned libs) genuinely fixed their 4 GB re-download complaint and is the
one piece worth keeping. **Effort: n/a (declined) — this list is the deliverable.**

### pyvideotrans (GPL-3.0 — clean-room functional specs, no code)

#### 1. Dub-length fitting (`videotrans/task/_rate.py`, ~lines 288–877)

The algorithm that makes dubs fit their slots. All constants verified against source:

| Parameter | Value | Meaning |
|---|---|---|
| Audio-only threshold (both-mode) | **1.2** | Required speedup ≤ 1.2× → audio absorbs everything, video untouched |
| Burden split beyond 1.2× | **50/50** | Joint target = slot + (dub − slot)/2, applied to both audio stretch and video slowdown; caps deliberately ignored in this branch |
| Max audio speed (audio-only mode) | 100 (default setting) | Effectively unlimited; past it, slot is overrun instead |
| Max video slowdown (video-only mode) | 10× | Past it, video target clamps to slot × 10, dub truncated later |
| Video retime bias | +0.005 | Slowdown factor padded to compensate frame-rounding undershoot |
| Retime no-op epsilon | 0.001 | Near-1.0 factors skip the filter |
| Min valid video clip | 1024 bytes | Smaller output = failed; retried without retiming, then dropped |
| Audio stretch clamp | 0.2–50.0 (rubberband); ≤2.0-step chained tempo filter as fallback | Pitch-preserving when rubberband present |
| Working audio format | 48 kHz / 2ch / 16-bit PCM | All silence + dub segments normalized |
| Dub-clip silence trim | threshold = clip dBFS − 20; min silence 100 ms; keep 80 ms head / 200 ms tail | Applied to every synthesized clip, default on |
| First-segment snap | < 100 ms start → cut from 0 | Avoids sub-frame clips when video slowdown is on |
| Video encode for cuts | x264 CRF 20, veryfast, **GOP=1**, yuv420p | Every frame an I-frame → clean concat boundaries |
| End-of-video freeze | last-frame clone pad = audio − video duration | Applied at final mux |

**Decision tree.** *Pre-pass (slack absorption):* every segment's slot end is
rewritten to the next segment's start — the silent gap after each line is donated to
that line; the last line's end becomes total media duration. Failed/missing dubs get
a silent placeholder exactly one slot long so the pipeline never stalls. *Per-line:*
dub ≤ slot → untouched. Otherwise in the combined mode: ratio ≤ 1.2 → audio-only
compress to exactly the slot; ratio > 1.2 → both sides meet at slot + overflow/2.
*Execution:* audio stretches run on a CPU process pool; a stretch is skipped if the
target exceeds current length (silence padding covers that). Each line becomes an
independent cut of the original video (seek, window, retime, hard duration limit);
a pre-roll clip covers 0 → first line; failed clips are re-cut without retiming, then
dropped; clips concat-copied in order. *Timeline regeneration:* the **measured**
duration of each generated clip (not the requested target) becomes the line's final
slot; a running cursor rewrites every subtitle to start at the cursor and end at
cursor + slot — subtitles track actual dub placement exactly; overruns are
hard-truncated when video slowdown is active, appended whole otherwise; short dubs
get tail silence. Overlapping segments never reach this stage (an ASR post-fix clamps
each line's end to the next line's start upstream). A TTS-only variant forces fit to
the slot and restores the *original* timeline on output.

#### 2. Per-subtitle-line clone references (`trans_create.py` ~882–1009)

Reference source priority: separated vocal stem → fresh mono 44.1 kHz extraction from
the original media. Cut timestamps are the line's **original** SRT start/end (before
gap-absorption rewriting). One 16 kHz/16-bit wav per line, indexed by line number;
the source-language subtitle text at the same index rides along as the ref
transcript. Thread pool of min(8, line count, CPU count); all cuts complete before
TTS starts. **Deliberately no min/max duration and no neighbor-borrowing** — a 400 ms
subtitle yields a 400 ms reference and quality degradation is accepted; engines that
can't handle it fail that line only (a dub job fails only when *zero* lines succeed).
Dub-file cache key: MD5 of text + role + rate + volume + pitch + channel id — lines
whose output already exists are skipped entirely.

#### 3. Second-pass ASR QC (`trans_create.py` ~419–482)

Runs after alignment, before final mux, opt-in, only when source ≠ target language
and the subtitle-embed mode doesn't require matched line counts. The assembled dub
track is downsampled to 16 kHz mono and re-recognized with **deliberately halved VAD
windows** (min speech halved with 500 ms floor; max speech halved; min silence halved,
clamped 50–1000 ms) so recognized lines come out short; short-line merging is
disabled. The resulting SRT **wholesale replaces** the target-language subtitle file —
recognized text wins unconditionally; the design accepts ASR drift in exchange for
frame-accurate display timing (dub audio is already rendered and untouched). Every
failure is a silent skip leaving the align-stage subtitles in place, so this stage
can never break a job. Engine falls back to faster-whisper large-v3-turbo when the
user's ASR channel can't do the target language.

*Our spec deviates here (see Spec 5): we want drift flagged, not silently accepted.*

#### 4. Translation cache + line-count validation (`videotrans/translator/_base.py` ~46–175)

Cache: one text file **per batch** (5 lines plain mode / 20 lines or whole-file SRT
mode), keyed by MD5 of channel id + API URL + srt-mode flag + model + source lang +
target lang + serialized batch content — any config or content change is the
invalidation mechanism (no TTL). Empty results are never cached; an all-empty batch
set raises. Line-count defense, plain mode: response split on newlines, extra lines
discarded, short responses padded with empty strings, then mapped 1:1 by index. SRT
mode: on count mismatch (LLM merged/split lines), recovery is **exact
timestamp-string matching** — translated cues map back by their time-range string;
unmatched source cues get empty text rather than shifting subsequent lines. Source
timeline is always authoritative. A final cosmetic pass strips the leading/trailing
ellipsis runs LLMs tend to add.

#### 5. Crash-isolated engine subprocesses (`videotrans/configure/base.py` ~170–276)

Every native-crashy stage (ASR, separation, diarization, VAD, retiming, stretch) runs
in a process pool with **spawn** start method and **one task per child** — every job
gets a fresh interpreter, memory fully returned, a segfault can't poison a warm
worker, and "restart" semantics are free (the pool replaces dead workers
automatically). CPU pool size: manual cap → min(cap, 8, CPUs); else
clamp(available-RAM-GB / 4, 2, 8). GPU pool defaults to 1 (strict serialization);
multi-GPU opt-in scales to min(GPU count, 8, CPUs). Progress transport: the child
overwrites a single-JSON log file; a parent daemon thread polls mtime every 1 s and
forwards to the UI bus; monitor gives up after ~1 h of no change. Child contract:
return (result, error); falsy result or non-empty error → typed task error. Native
death → the pool's broken-pool exception, decorated with model name + GPU index
before surfacing. GPU selection: first card with > 24 GB free VRAM, else most-free;
CUDA re-verified at submit time, unavailable → kwargs rewritten to CPU.

#### 6. Batch wave control (`videotrans/task/mult_video.py`, `job.py`)

Nine FIFO stage queues (prepare → recognize → diarize → translate → dub → align →
second-pass ASR → assemble → done), each with dedicated workers. GPU-heavy stages get
1 thread by default (2 with 2–3 GPUs in multi-GPU mode, 4 with ≥ 4); network-bound or
globally stateful stages (translate, dub, align, second-pass, done) are always 1.
With waves off, all videos enter the conveyor at once — video A can be translating
while B is in ASR. With `batch_nums` > 0, the file list is chunked; a dispatcher
busy-waits (1 s ticks) until every task in a chunk finishes or is stopped before
releasing the next — bounding peak temp-disk and VRAM at the cost of inter-wave
overlap. Inside the dub stage, lines fan out on a thread pool (`dubbing_thread`,
default 1); per-line errors are collected and a job fails only if zero lines succeed.
Stage workers convert any exception into a stage-prefixed UI error and mark the task
ended, so a wave can never hang on a failed member.

#### 7. Fact-check: the pyvideotrans ↔ VoiceStudio integration

`videotrans/tts/_omnivoice.py` (~12–77) speaks **Gradio, not REST**: it builds a
`gradio_client.Client` against a user-pasted URL and calls the named endpoint
**`/_clone_fn`** with text, a natural-language language name (~35 ISO codes mapped),
a per-line reference wav + transcript, and a knob set (steps/guidance/denoise/
duration/post-process flags) that matches a 12-input Gradio clone function from an
older or forked VoiceStudio build. It expects a filesystem path to a wav back.

**Verdict: broken against current VoiceStudio.** Our backend is FastAPI on
:3900 (`backend/main.py`) with REST routers; we expose no Gradio app and no
`/_clone_fn` (our only Gradio surface is the optional SoniTranslate *subprocess* on
:7860 — a different application). A gradio client pointed at :3900 fails fetching
the Gradio config, which lands in pyvideotrans's fatal "Could not fetch config"
branch — users get an immediate hard stop. Their tracker already has an open bug
against this integration. Remedies in Spec 11.

### Patter (MIT — portable)

#### 1. Sentence chunker (`libraries/python/getpatter/services/sentence_chunker.py`, 565 LOC)

Streaming sentence segmentation for low-TTFA TTS. Accumulates tokens; `push(token)`
returns zero or more complete sentences. Boundary detection is regex
marker-replacement: protect non-terminal periods (honorifics for EN/IT/ES/DE/FR/PT,
website TLDs, decimals, ellipses, initials, acronym chains, company suffixes), mark
real terminators, split. Terminator tables cover Latin + CJK + 8 non-Latin scripts
(Devanagari, Arabic, Armenian, Ethiopic, Khmer, Burmese, Tibetan). Three emission
paths: standard (≥ min length and > 1 sentence → emit all but the buffered tail);
**short flush** for instant single-sentence replies (guards: one terminator, ≥ 1 word,
no preceding digit, no ALL-CAPS acronym tail, no honorific tail); **aggressive
first-clause flush** (opt-in, first clause of each turn only) on soft punctuation
`, — –` at ≥ 40 chars — claimed 200–500 ms TTFA savings — with seven guards
(decimals, currency within 8 chars, unbalanced brackets/quotes, ellipsis, sub-token
ambiguity) and a hard disable for Italian (comma = decimal separator). Constants:
`min_sentence_len=20`, `aggressive_first_min_len=40` (comment: below ~40 chars hurts
prosody; ElevenLabs buffers ~120 internally). `flush()` emits the remainder;
`reset()` discards (barge-in). Deps: stdlib `re` only. A TS mirror + golden parity
scenarios ship alongside.

**Porting:** feeds `/ws/tts` (`tts_stream.py`) so text synthesizes sentence-by-
sentence ahead of the PCM stream; aggressive first-flush directly serves the
< 100 ms TTFA goal. Distinct from `subtitle_segmenter.py` (offline length-balancing)
but the terminator/honorific tables could become shared constants. Port the parity
scenarios as pytest fixtures. **Effort: S.**

#### 2. NLMS acoustic echo canceller (`libraries/python/getpatter/audio/aec.py`, 333 LOC)

Time-domain sample-by-sample NLMS adaptive filter with leakage + frame-wise Geigel
double-talk detector. Far-end (TTS) PCM feeds a ring buffer; near-end (mic) frames
get `e = near − w·x` as output, with the weight update frozen during double-talk
(`max|near| > 0.6 · max|far|`) and when the far reference is near-silent (≤ −60 dBFS
— raised from a smaller epsilon after a weight-blowup bug). Two-phase step schedule:
mu 0.5 for the first 0.5 s, then 0.1. Pass-through guards: far buffer not primed,
and a **250 ms staleness window** — no recent far-end push → pass through rather
than convolve against a frozen reference (was producing an audible buzz during
silence). Defaults: **512 taps** (= 32 ms @ 16 kHz; 2048 tested → 8–12 s convergence,
rejected), leakage 0.9999, far buffer 0.5 s. I/O: int16 mono PCM, **8 or 16 kHz
only**, not thread-safe (one instance per session). Self-declared limitations: no
frequency-domain partitioning, no residual-echo suppressor, no delay estimation —
docstring recommends libwebrtc AEC3 for production-grade. Deps: numpy.

**Porting:** sits ahead of `/ws/transcribe` in `capture_ws.py` — `push_far_end()`
fed from every chunk `/ws/tts` ships, `process_near_end()` on mic frames. The real
work: our TTS streams 24 kHz while the canceller accepts 8/16 kHz (resample the
far-end to the capture rate, or relax the check and scale taps); the staleness clock
must track *playout* time, not send time (desktop speaker latency ≠ carrier RTT);
rho likely needs tuning for loud desktop-speaker bleed. Pure numpy → identical
default behavior on all three platforms. **Effort: M** (the port is an hour;
sample-rate plumbing, delay alignment, and tuning are the work).

#### 3. LLM-judge eval harness (`libraries/python/getpatter/evals/`, 1,636 LOC)

YAML/JSON suites of `EvalCase {name, turns, expected_behavior, rubric, tags}`;
per-case error containment (a mid-case exception keeps the partial transcript and
still judges it; a judge failure records score 0 + reasoning instead of aborting the
suite). Judge: chat-completions with JSON response format, temperature 0, pass
threshold 0.7 — with two hardening details worth copying verbatim: tolerant JSON
parsing (strips code fences; invalid JSON → fail with reasoning), and **the verdict
is recomputed locally** (`passed = score >= threshold`) because trusting the model's
self-reported `passed` once let a hallucinated pass through at score 0.2. A
deterministic chainable assertions layer (`expect(...).tool_called(...).judge(...)`)
raises plain `AssertionError`s so pytest reports work. CLI exits non-zero unless all
cases pass — CI-gateable. The judge backend is injectable (any object with
`judge(prompt)`).

**Porting:** complements our deterministic stack — probe judges (`tests/probe/judges/`)
score DSP correctness, `omnivoice/eval/` scores WER/MOS/speaker-sim; Patter's harness
adds the missing *semantic* tier (dub translation naturalness, dictation-correction
quality). Keep our "no LLM on the verdict path" rule for CI gates: run LLM-judge
suites as a separate **non-gating** job. Swap the judge backend for our
`llm_backend.py` (local model, keeps local-first). Port `case.py` + `runner.py` +
`assertions.py` nearly verbatim; drop `session.py` (telephony) in favor of our probe
Actor. **Effort: M.**

#### 4. Docs-drift CI (`.github/workflows/docs-feature-drift.yml` 112 LOC + checker 211 LOC)

Daily cron (03:00 UTC) + manual dispatch; `contents: read, issues: write`. Three-way
cross-reference: canonical feature inventory (theirs is an xlsx in a private sibling
repo — their weakest design point, soft-failing when the token is missing) × docs
filename stems × regex-parsed public SDK exports. Three drift buckets; only
inventory↔docs mismatches gate (export drift is report-only). The best part is the
**issue automation**: on failure, look up the open `docs-drift`-labeled issue and
**update its body in place** (single rolling issue, no spam); create it if absent;
on success, comment "drift resolved" and auto-close. Idempotent and self-healing.

**Porting:** we already gate `docs/install/*.md` via `scripts/validate-install-docs.py`
in ci.yml — this is the second half. Replace the private-repo xlsx with an
**in-repo** canonical inventory (a checked-in `features.yaml`, or generated from the
engine registry + FastAPI route table); adopt the rolling-issue pattern verbatim.
Note this auto-filed issue is maintainer-facing CI, distinct from the user-facing
opt-in bug reporter. **Effort: S** (workflow is copy-adapt; defining the inventory is
the only design decision).

#### 5. Consent-bounded telemetry (reference design only; `getpatter/telemetry/`, 1,235 LOC)

**Not adopting telemetry** — Patter's default is opt-OUT with a third-party endpoint,
both of which violate our local-first constraint. What transfers to our opt-in
prefilled-URL GitHub bug reporter (CLAUDE.md Capability 2):

1. **Two-layer key+value allowlist** before an event is built: unknown keys dropped,
   values checked against closed enums with off-list values coerced to `"other"` —
   making a leaked custom name "structurally impossible to emit, even from a buggy
   caller". Stronger than regex-scrubbing after the fact; directly implements our
   planned token/key/home-path exclusions for the issue body.
2. **Model-name sanitization**: anything with separators, whitespace, or > 40 chars
   (fine-tune IDs, self-hosted paths) collapses to `"{vendor}-other"`; date suffixes
   stripped; final shape re-checked by regex. Apply the same to user voice-profile
   and engine names in bug reports.
3. **Coarse buckets**: counts → `{0, 1, 2_3, 4_6, 7_12, 13_plus}`, versions →
   major.minor only, OS → family only, arch → `x86_64/arm64/other` — explicitly
   anti-fingerprinting.
4. **Precedence-ordered consent resolver**, inverted to opt-IN for us (default OFF;
   Settings toggle → marker file; `DO_NOT_TRACK` honored as an absolute OFF), with
   the invariant "checking consent never writes to the filesystem".

**Effort: S** for the allowlist/sanitizer port (~250 LOC of pure stdlib functions);
the reporter UI around it is separate work.

---

## Implementation specs

*Each ranked action mapped onto this codebase: integration points, shapes, and a
test plan. File references verified against main as of 2026-06-11. These are
work-item-grade specs, not designs — the implementer still owns the details.*

### Spec 1 — Dub-length fitting v2

**Goal:** replace "trust the math + onset snap" with the measured-fit algorithm from
the functional spec above (reimplemented; do not open pyvideotrans source).

- **Files:** new `backend/services/length_fit.py` (the decision tree); integrate in
  the dub orchestration in `backend/services/dub_pipeline.py` after TTS, before mux;
  `backend/services/speech_rate.py` keeps its role as the *pre-generation* estimator
  (LLM trim/expand to fit the slot) — length-fit is the *post-generation* enforcer;
  `backend/services/onset_align.py` stays (it solves start alignment, not duration);
  video cuts/retime/concat via `backend/services/ffmpeg_utils.py` (respect the
  ffmpeg semaphore and `register_proc` for abortability).
- **Design deltas vs pyvideotrans:** keep their 1.2× audio-only threshold and 50/50
  burden split as defaults but expose both in dub settings; integrate with our
  incremental re-dub — slack absorption must be computed over the *full* segment
  list even when only stale segments regenerate (`backend/services/incremental.py`
  fingerprints must include the two new knobs in `_GEN_INPUT_FIELDS`, since they
  affect output); time-stretch with pitch preservation (rubberband when available,
  chained ffmpeg tempo fallback) under the existing GPU/CPU job queue.
- **Subtitle regeneration:** rewrite segment times from measured clip durations with
  a running cursor (as specced) before `dub_export.py` renders SRT/burn-in.
- **Tests:** probe spec `dub_export.probe.yaml` + `tests/probe/judges/dubbing.py`
  already gate segment duration ratio [0.5–1.6×] — add a judge check that final
  audio fits final video ±1 frame and that regenerated SRT cue times equal measured
  placements; unit tests for the decision tree at the 1.2 boundary, zero-length
  slots, last-segment, and overlap-clamped inputs.
- **Effort: L.** Land the audio-only path first (pure win, no video retime), video
  slowdown second.

### Spec 2 — MCP server v1

**Goal:** ship the missing half of our MCP story: mounted endpoint + per-agent voice
binding + stdio shim (ported from voicebox, MIT attribution).

- **Files:** `backend/mcp_server.py` (existing FastMCP: keep tools, add
  `transcribe`); mount on the main app in `backend/main.py` with lifespan
  composition (wrap existing startup hooks via AsyncExitStack — do not replace);
  new middleware + `ContextVar` for `X-VoiceStudio-Client-Id`; new alembic migration
  for `mcp_client_bindings {client_id, label, profile_id, default_engine,
  last_seen_at}` (additive — satisfies the backward-compat constraint); REST CRUD
  router `backend/api/routers/mcp_bindings.py`; new `backend/mcp_shim/` (port
  nearly verbatim — httpx-only stdio↔HTTP proxy); Settings UI section in
  `frontend/src/pages/Settings.jsx`; update `docs/mcp.json` + `docs/` MCP doc.
- **Resolution chain:** explicit tool arg → client binding → global default profile
  → helpful error. Copy voicebox's loopback-only gate for any file-path-accepting
  tool argument.
- **Tests:** pytest for the resolve chain + middleware; probe spec addition driving
  `speak`/`transcribe` over the mounted endpoint; shim smoke test against a live
  backend.
- **Effort: M** (S shim, M mount + bindings + Settings).

### Spec 3 — Dictation refinement

**Goal:** refined-by-default-quality dictation: deterministic artifact collapse for
everyone, LLM filler-word removal for users with a local LLM configured.

- **Phase 1 (S, no LLM):** port `collapse_repetitive_artifacts()` (word-level ≥6
  repeats; char-level 2–60-char units ≥6, catches no-space scripts) into a new
  `backend/services/refinement.py`; apply to final transcripts in
  `backend/api/routers/capture_ws.py` after `_transcribe_buffer`, never to partials.
  Port voicebox's test corpus pattern.
- **Phase 2 (M):** prompt builder with the three toggles (`smart_cleanup`,
  `self_correction`, `preserve_technical`), few-shot examples as structured
  `messages` pairs, executed through `backend/services/llm_backend.py`
  (`get_active_llm_backend().chat(...)`, timeout-bounded). Readiness = active LLM
  backend reachable; on any failure the raw transcript stands. Persist both raw and
  refined in the `transcriptions` table (`backend/core/db.py`, additive column via
  alembic) so history supports re-refine. WS protocol: `{type:"final", text,
  refined_text?, ...}` — frontend pastes `refined_text ?? text`.
- **Settings:** auto-refine toggle (default ON only when an LLM backend is active —
  cross-platform default behavior stays identical: no LLM → identical pass-through
  everywhere) + the three flag toggles; i18n keys for all labels.
- **Tests:** unit tests for the collapse pass (incl. CJK-free fixtures using Latin
  repetition patterns); contract test that a dead LLM endpoint yields the raw
  transcript within timeout.

### Spec 4 — Per-segment clone refs

**Goal:** per-line prosody matching — cut each dub segment's clone reference from
the separated vocal track at that segment's own timestamps.

- **Files:** `backend/services/speaker_clone.py` — add
  `extract_segment_refs(vocals_path, segments, out_dir)` alongside the existing
  per-speaker `extract_speaker_clones()`; dub pipeline passes the per-segment ref to
  the TTS call when the engine supports reference audio.
- **Design deltas vs pyvideotrans:** unlike their no-floor policy, keep a quality
  floor — segment shorter than `MIN_REF_DURATION_S` falls back to the existing
  per-speaker 5–15 s reference (we already have it; they don't). Use original
  (pre-slack-absorption) segment times. Cut with the thread pool pattern already
  used in the pipeline; ride the source-language text along as ref transcript.
- **Mode:** per-segment refs default ON with per-speaker fallback; expose a dub
  setting to force per-speaker (long-form consistency sometimes beats per-line
  prosody). Add the mode to `incremental.py` `_GEN_INPUT_FIELDS`.
- **Tests:** unit test slicing math + fallback threshold; probe `voice_clone` /
  `dub_export` flows with a two-speaker fixture asserting each segment got a ref
  file of its own span (or the fallback).
- **Effort: S–M.**

### Spec 5 — Second-pass ASR QC

**Goal:** measured subtitle truth + automatic dub-quality flagging.

- **Files:** new stage in `backend/services/dub_pipeline.py` after assembly, before
  export; reuse `backend/services/asr_backend.py` (active backend; fall back to
  WhisperX defaults when the active backend can't do the target language); job
  events via `backend/core/job_store.py` `append_event`.
- **Design deltas vs pyvideotrans:** they let recognized text *replace* subtitles
  unconditionally; we keep generated text authoritative for *content* and use the
  second pass for *timing* + *QC*: re-recognize the dubbed track with halved VAD
  windows, re-time cues from recognized boundaries, and compute per-segment drift
  (normalized WER between recognized and target text — scorer exists in
  `omnivoice/eval/wer/`). Segments above a drift threshold get flagged in the job
  events and surfaced in `DubTab.jsx` as "verify this line" markers feeding the
  incremental re-dub loop. Stage is opt-out, never fatal: any failure leaves
  align-stage subtitles in place.
- **Tests:** pipeline test with an injected mispronounced segment asserting the flag
  fires; probe judge asserting second-pass SRT stays well-formed and within the
  existing dubbing duration-ratio gates.
- **Effort: M.**

### Spec 6 — MLX runtime pass

**Goal:** Apple Silicon speedup via dual-runtime routing, no behavior divergence.

- **Files:** `backend/services/tts_backend.py` — adopt voicebox's backend-aware
  model mapping inside the registry: one engine key resolving to
  `mlx-community/...` vs upstream weights by platform probe; harden the probe to
  `import mlx.core` catching `ImportError, OSError, RuntimeError` (PyInstaller
  bundles); `backend/services/asr_backend.py` — route dictation + dub ASR through
  the existing `MLXWhisperBackend` when the probe passes (today it exists but isn't
  the default path on Apple Silicon).
- **Constraint check:** implementation-level only — output behavior, defaults, and
  UI identical on all platforms (explicitly allowed by the parity rule).
- **Tests:** probe `engines` spec on macOS runner asserting MLX route is selected
  and produces passing DSP judges; regression: CUDA/CPU platforms unaffected
  (registry resolution unit tests with mocked probes).
- **Effort: L** across engines; ship Whisper-first (M) since `MLXWhisperBackend`
  already exists.

### Spec 7 — Crash-isolated ASR

**Goal:** a native ASR crash becomes a failed job, never a dead backend.

- **Files:** generalize `backend/services/subprocess_backend.py` (today TTS-oriented:
  length-prefixed JSON, GPU slots, op allowlists) with an ASR sidecar subclass —
  `sidecar_script()` wrapping the crashy engine; wire as an `ASRBackend`
  implementation in `asr_backend.py`.
- **Design choice vs pyvideotrans:** they use one-task-per-child process pools
  (fresh interpreter per job — max isolation, max model-reload cost). Our sidecar is
  long-lived with a handshake + health check. Hybrid: keep the long-lived sidecar
  for warm-model latency, add their **automatic respawn-on-death** semantics —
  parent detects EOF/broken pipe, marks the in-flight job failed with a decorated
  error (engine + device, like their broken-pool message), respawns lazily on next
  request. Their single-JSON progress-file pattern is unnecessary here — we already
  have a frame protocol with `progress` ops.
- **Tests:** kill the sidecar mid-transcription in a pytest (send SIGKILL) — assert
  job fails with the decorated error, backend stays healthy, next request respawns;
  smoke-test addition for the respawn path.
- **Effort: M.**

### Spec 8 — Streaming polish kit

**Goal:** production-grade `/ws/tts` TTFA + dictation-during-playback.

- **Chunker (S):** port Patter's `sentence_chunker.py` (MIT) to
  `backend/services/sentence_chunker.py`; use in `backend/api/routers/tts_stream.py`
  to synthesize sentence-by-sentence and flush the first clause aggressively
  (≥ 40 chars on soft punctuation, their seven guards, Italian comma disable).
  Port their golden parity scenarios as pytest fixtures. Extend terminator/honorific
  tables toward our language list; share constants with `subtitle_segmenter.py`
  where they overlap.
- **AEC (M):** port Patter's `aec.py` (MIT) to `backend/services/aec.py`; in
  `capture_ws.py`, feed `push_far_end()` from the audio `/ws/tts` ships (resampled
  24 kHz → capture rate) and run `process_near_end()` on mic frames before
  transcription. Key adaptations (from the deep dive): staleness clock must track
  playout time, not send time; Geigel rho needs desktop-speaker tuning; one AEC
  instance per ws session (not thread-safe). Behind a Settings toggle initially
  ("dictate during playback"), defaulting ON only once probe-verified on all three
  platforms — until then it's opt-in, per the platform-default rule.
- **Tests:** chunker parity fixtures; AEC unit tests (echo-only input converges to
  near-silence; double-talk freezes adaptation); probe dictation spec variant with
  synthetic far-end playback.
- **Effort: M total.**

### Spec 9 — Quality rails

**(a) Docs-drift CI (S).** New scheduled workflow alongside
`.github/workflows/ci.yml` (daily cron + dispatch, `issues: write`): a checked-in
canonical inventory (`docs/features.yaml` — engines, capabilities, platform flags;
seed it from the README feature grid + engine tables) diffed against docs stems and
the engine registry (`list_backends()` output). Reuse the self-test pattern from
`tests/scripts/test_validate_install_docs.py` for the new checker. Adopt Patter's
rolling-issue automation verbatim: update one `docs-drift`-labeled issue in place,
auto-close on green. Existing `scripts/validate-install-docs.py` stays as the
PR-gating half.

**(b) LLM-judge eval tier (M).** Port Patter's `case.py` + `runner.py` +
`assertions.py` into `tests/evals/` with the judge backend swapped to
`llm_backend.py` (local model — keeps local-first). Hard rule preserved: **LLM
judges never gate CI** — they run as a separate non-blocking scheduled job whose
report lands as an artifact; deterministic probe judges remain the only gates. First
suites: dub translation naturalness (segments from the probe dub fixture) and
dictation-refinement quality (Spec 3 outputs). Copy their two hardening details:
recompute pass locally from score; tolerant JSON parsing.

### Spec 11 — pyvideotrans bridge

**Goal:** restore the inbound bridge from a 17.9k★ upstream integrator.

- **Option A — upstream a REST integration (preferred, S–M):** PR to pyvideotrans
  replacing the Gradio call in their `_omnivoice.py` with our REST API (clone-TTS
  endpoint or the OpenAI-compatible surface in
  `backend/api/routers/openai_compat.py`). They already ship OpenAI-style TTS
  channels, so the precedent exists. Friendly-fork etiquette: file their open
  integration bug first, reference it.
- **Option B — Gradio compat shim (fallback, M):** a tiny optional Gradio app in our
  backend exposing a `/_clone_fn`-compatible signature that proxies to our REST
  pipeline. Only if upstream declines — it adds a gradio runtime dep for one
  integration and another surface to keep compatible.
- **Either way:** add a contract test pinning whatever surface they consume, so the
  bridge can't silently break again (the engine-compat constraint extended to an
  external consumer).

### Specs 12–13 — voice mixing · dynamic engine lifecycle (sized, not yet designed)

**12 (S–M):** weighted voice mixing from Kokoro-FastAPI (Apache-2.0) — blend
embeddings/style vectors by ratio where the engine exposes them; surface as a
"blend" control in `CloneDesignTab.jsx`. Engine-dependent: start with the preset
engines whose voice representations are vectors.
**13 (M):** Speaches-style (MIT) per-request model lifecycle — idle-unload timers
and VRAM-pressure eviction layered on the existing `gpu_queue` + `unload()`
contract in `tts_backend.py`/`asr_backend.py`. Design doc first: interaction with
the GPU slot accounting in `subprocess_backend.py` is the tricky part.

*Actions 10 and 14 are practice/positioning items — no code spec needed; 14's
content is in [positioning moves](#positioning-moves).*

---

## Roadmap directions (community discussion #346)

*Researched 2026-06-12 (third pass, four research agents + five verification
sub-agents). The maintainer's [discussion #346](https://github.com/debpalash/VoiceStudio/discussions/346)
announced a feature roadmap toward full ElevenLabs feature-parity. This section
grounds each direction in the landscape: what exists, what's license-clean, what
the honest constraints are, and a scope ladder per direction.*

**Mapping the announcement to this doc** — several items are already covered:

| Discussion item | Status |
|---|---|
| Unlimited-length generation | Covered — voicebox chunked TTS ([deep dive](#1-chunked-long-form-tts-backendutilschunked_ttspy-299-loc), port, S) |
| WisprFlow-like dictation for agentic/code editors | Covered — [Spec 3](#spec-3--dictation-refinement) + [positioning move 4](#positioning-moves) |
| Polished dubbing experience | Covered — [Specs 1](#spec-1--dub-length-fitting-v2), [4](#spec-4--per-segment-clone-refs), [5](#spec-5--second-pass-asr-qc) |
| Better MLX / Nvidia / AMD / CPU | [Spec 6](#spec-6--mlx-runtime-pass) + new compat-matrix research (§R4) |
| Polished OpenAPI specs with Scalar | **Mostly shipped** — Scalar mounted at `/docs` since #307; remaining work is spec hygiene (§R2) |
| Agentic voice workflow | New — §R1 |
| Remote GPU · Tailscale · remote API in UI | New — §R2 |
| Ebook/audiobook/stories creator · persona gallery | New — §R3 |
| Better model & env management | New — §R4 |

### R1 — Agentic voice workflow

**Runtime landscape (licenses verified against LICENSE files, 2026-06):**

| Runtime | License | Fit |
|---|---|---|
| [pipecat](https://github.com/pipecat-ai/pipecat) (12.8k★, v1.0) | **BSD-2** | **Best fit.** A Python library that runs *inside* our existing FastAPI process (`FastAPIWebsocketTransport`) — no extra server. Local VAD (Silero) + smart turn detection + barge-in. Its `OpenAITTSService` takes a `base_url` and defaults to 24 kHz — our `/v1/audio/speech` plugs in with configuration, not code |
| [LiveKit Agents](https://github.com/livekit/agents) (10.9k★) | Apache-2.0 | Good, heavier: needs a LiveKit media server alongside. The right choice only if self-hosted SIP at scale becomes the priority (their [SIP server](https://github.com/livekit/sip) is Apache-2.0). Their openai plugin TTS/STT classes accept `base_url` (verified in source) — OVS works as a provider today |
| [Patter](https://github.com/PatterAI/Patter) (MIT) | MIT | Parts donor (already deep-dived). If embedded, its **opt-out telemetry must be hard-disabled** to honor our local-first guarantee |
| [vocode-core](https://github.com/vocodedev/vocode-core) | MIT | **Avoid as runtime** — no commits since Nov 2024 |
| [TEN Framework](https://github.com/TEN-framework/ten-framework) | Apache-2.0 **+ conditions** | **Disqualified**: LICENSE bans hosting on "End User devices" (fatal for a desktop app) + an Agora non-compete |

**Telephony honesty.** There is **no fully-local path to the PSTN** — reaching a real
phone number requires a carrier (Telnyx ~$0.005–0.007/min, Twilio ~$0.014/min; even
self-hosted Asterisk/FreeSWITCH needs a SIP trunk as the gateway). So under our
constraints, outbound calling must be an **explicit opt-in integration where the user
supplies carrier credentials** — never a default. Two prerequisite spikes before
promising calls: (a) TTFA benchmark of our engines in a streaming pipeline against
the ~600 ms p95 voice-to-voice budget; (b) AudioSeal detection survival through the
8 kHz G.711 phone leg (untested anywhere — phone-band downsampling may strip the
watermark, and it certainly reduces cloned-voice fidelity).

**Persona/community bots.** Prior art exists but is assembled hobby-grade
(closest: [Discord-Local-LLM-VoiceChat-Bot](https://github.com/KickerMix/Discord-Local-LLM-VoiceChat-Bot)
— local Whisper + LM Studio + cloning). Text-persona bots are trivial on our stack
(LLM adapter + `/v1/audio/speech` voice replies). Live voice-channel bots are harder:
discord.py has never shipped voice *receive* (years-open RFC); the working options
are Pycord's recording sinks or [discord-ext-voice-recv](https://github.com/imayhaveborkedit/discord-ext-voice-recv)
(both MIT, both single-maintainer risk). The conversation loop (VAD/turn-taking)
should come from pipecat, not from this prior art.

**Safety/regulatory (binding, not optional).** FCC ruling
[FCC 24-17](https://www.fcc.gov/document/fcc-makes-ai-generated-voices-robocalls-illegal)
(Feb 2024): AI/cloned voices are "artificial" under the TCPA — consumer calls require
prior express consent ($500–1,500/call private right of action). Texas SB 140
requires AI disclosure within the first 30 seconds of a call. Tennessee's ELVIS Act
extends liability to **tool providers**. **EU AI Act Article 50 applies from
2026-08-02**: people must be told they're talking to an AI, and generative-audio
output must be marked machine-readably — **the open-source exemption does not cover
Article 50**, and our AudioSeal default maps directly onto the marking obligation
(a structural advantage no competitor ships). The "my own cloned voice, my own
errand" single-call case is a genuine legal gray zone — docs should say so rather
than imply it's safe.

**Guardrails to build in (concrete):** (1) non-removable disclosure preamble on every
outbound call — satisfies Texas + FCC direction + EU Art 50(1) in one stroke;
(2) **consent-locked voice profiles** — agentic features require a profile flagged
verified-own-voice (recorded consent phrase), exactly the lock voicebox was
criticized for lacking; (3) AudioSeal always-on for agentic output, no toggle;
(4) destination allowlist + daily call cap, and no bulk-dial API surface ever —
architecturally incapable of being robocall infrastructure; (5) local immutable call
log (with two-party-consent warning before audio recording); (6) honest jurisdiction
notice in docs.

**Scope ladder:** **v1 (S–M, mostly docs):** OVS as TTS/STT provider for
pipecat/LiveKit — both verified to point at `localhost:3900/v1` via `base_url`
today; ship a `docs/agentic-voice.md` recipe + a pipecat smoke test, fix whatever
param mismatches it exposes. Users wire their own agent; we stay a model server.
**v2 (M–L):** built-in Discord persona bot (opt-in by construction — user supplies
their own bot token; identical on all platforms): text replies via the LLM adapter +
voice replies via `/v1/audio/speech`, persona attached to a consent-locked profile;
live voice-channel mode as a stretch. Mount the MCP server in the same milestone
(Spec 2) so external agents can drive OVS voices. **v3 (L, only after guardrails
1–5 exist):** telephony via opt-in carrier credentials, pipecat embedded with
Telnyx/Twilio serializers — disclosure preamble, watermark, allowlist, and call log
land in the same PR, not a follow-up.

### R2 — Remote GPU, Tailscale, remote API, Scalar

**The pattern is settled** across Ollama / LM Studio / Open WebUI / Jellyfin: server
binds a port, client has a *base URL* setting, optional bearer key. Nobody
comparable ships custom tunneling — LM Studio's remote story (LM Link, June 2026)
took a **Tailscale partnership on tsnet** to do more, which is exactly the bar we
should not chase. The existing Tauri app *is* the thin client; it needs a Backend
URL setting + `/health` handshake, with the local backend supervisor disabled when
remote.

**Security is the non-negotiable half.** The cautionary tale:
[~175,000 publicly exposed no-auth Ollama servers](https://thehackernews.com/2026/01/researchers-find-175000-publicly.html)
found in early-2026 scans, with documented LLMjacking. Our voice-cloning endpoints
are *more* sensitive than chat. The consensus mechanism (LM Studio, vLLM, Speaches):
optional bearer key — `OMNIVOICE_API_KEY`; when set, all non-loopback HTTP+WS
requires `Authorization: Bearer`. Our existing `NetworkAccessMiddleware` PIN gate
has the right ASGI shape and needs a bearer variant. Loopback-only stays the desktop
default. Note: Tailscale Serve terminates on-node and forwards from `127.0.0.1` —
Serve traffic looks loopback to the PIN gate, so the token must still apply in
server mode. Docs say plainly: bearer-over-plain-HTTP is sniffable; use Tailscale
(WireGuard) or Serve (TLS) beyond a trusted LAN; never Funnel without the key.

**Tailscale depth:** ship rung (a) — documentation ("install Tailscale both ends,
paste the MagicDNS URL"), plus a Serve recipe — which is all Home Assistant, Open
WebUI, and Jellyfin actually ship. Embedding is not viable from Python: tsnet is
Go-only; libtailscale's Python binding and tailscale-rs are explicitly
experimental/unaudited and not on PyPI. Mention [headscale](https://github.com/juanfont/headscale)
for users wanting a fully open control plane. Tailscale's client core is BSD-3;
documenting it imposes nothing on us.

**Remote LLM endpoint UI:** vLLM's OpenAI-compat server is verified drop-in for our
`llm_backend.py` (today env-only via `TRANSLATE_BASE_URL`) — the work is Settings
fields for base URL + model + optional API key, which Ollama ignores and
vLLM/LM Studio require. Watch item: **vLLM-Omni** now serves TTS first-class with an
OpenAI-compatible `/v1/audio/speech` — including **CosyVoice3, an engine we wrap** —
so "OVS on the GPU box" will eventually compete with "vLLM-Omni on the GPU box"; a
future option is consuming a remote vLLM-Omni endpoint *as an engine*.

**Scalar:** already shipped (#307 — mounted at `/docs`, `scalar-fastapi` is MIT,
actively maintained). The remaining "polished spec" work is OpenAPI hygiene Scalar
renders but can't create: stable `operation_id`s, router tags + descriptions,
`response_model` + examples on every endpoint — `/v1` and core TTS routes first,
since those are what remote users hit. ~1 day for tags/IDs; the response-model long
tail is incremental.

**Ladder:** (1) Backend URL setting + health handshake — S; (2) bearer token incl.
WS paths + tests — S–M; (3) "Remote GPU over Tailscale" docs page — S;
(4) remote LLM endpoint UI — S–M; (5) OpenAPI hygiene — M incremental.
**Don't build:** custom tunneling/relay, tsnet embedding, Funnel as a promoted
path, mTLS/OAuth (overkill vs bearer + WireGuard), a second thin-client binary.

### R3 — Audiobook/stories creator + persona gallery

> **Status (2026-06-13):** backend shipped — `backend/services/audiobook.py`
> parses a chapter-delimited script (Markdown `# H1` chapters + inline
> `[voice:NAME]`; `[pause …]` delegated to the shared `parse_pause_markers`)
> into a chapter/span plan, renders each chapter through the active TTS engine
> (`synthesize_chapter` + `chunked_tts`), and muxes a chapterized **m4b**
> (FFMETADATA1 chapters). `POST /audiobook/plan` previews the plan;
> `POST /audiobook` runs the synth job streaming SSE progress (ffmpeg-gated).
> A dedicated **Audiobook** tab (script editor → plan preview → streamed synth
> → m4b player/download) ships in the frontend. Deferred: epub/pdf/docx ingest,
> ACX `loudnorm` mastering, and crash-resume.

**The production bar** (verified against [ebook2audiobook](https://github.com/DrewThomasson/ebook2audiobook),
audiblez, epub2tts, abogen, Pandrator): broad ingest (epub/mobi/pdf/docx + OCR for
image PDFs), chapter detection (TOC-driven for epub — even the 19.2k★ leader's
algorithm is thinly documented), **chapterized m4b** output (FFMETADATA1 chapters +
cover via `attached_pic` — ffmpeg writes both Nero and QuickTime chapter forms),
inline tags (`[pause:N]`, `[voice:...]` — ebook2audiobook's square-bracket dialect
is Apache-2.0 and portable), batch with per-file voice mapping, and **crash resume**
(their `--session` pattern). The mastering bar is the
[ACX technical spec](https://help.acx.com/s/article/what-are-the-acx-audio-submission-requirements):
RMS −23 to −18 dB, peaks < −3 dB, noise floor < −60 dB RMS, ≥192 kbps CBR MP3,
≤120 min/file, room tone 1–5 s both ends — implementable as two-pass ffmpeg
`loudnorm` + an `astats` verifier. **Framing caveat:** ACX itself prohibits AI
narration unless authorized — market this as "masters to ACX technical spec," never
"Audible-ready."

**License traps in the parser stack (verified — this is the §R3 landmine):**
`ebooklib` is **AGPL-3.0 with no commercial-license option** — it poisons our
commercial build; parse EPUB with `zipfile` + `lxml` instead (EPUB is zip+XHTML).
PyMuPDF is AGPL (Artifex sells exceptions) — use **pypdf (BSD-3)**. The PyPI `mobi`
package is GPL — shell out to Calibre's `ebook-convert` instead (process boundary is
safe). OCR via pytesseract/tesseract is Apache-2.0, clean.

**What we already have:** voicebox's Stories schema (deep-dived above) is the
assembly/timeline half. The missing halves are symmetric: ingest (parsing,
chapterization, long-form batch with per-chapter resume) and export (m4b + ACX
mastering). **Ladder:** A1 EPUB ingest + TOC chapters + resumable per-chapter TTS
queue (M) → A2 chapterized m4b export (S) → A3 ACX mastering pass (S–M) → A4
PDF/txt/docx (M) → A5 inline tags + per-chapter voices (M) → A6 OCR + Calibre
shell-out (M) → A7 book→Stories timeline round-trip (L) — the differentiator no
surveyed tool has.

> **Status (2026-06-13):** the browse-preview-install surface already exists —
> `VoiceGallery.jsx`'s Community zone over the `omnivoice-gallery` git manifest
> (`backend/api/routers/community.py`), plus `.omnivoice` bundle export/import
> (`marketplace.py`). The missing piece for the synthetic-only gate was data
> integrity: imported community presets and bundle round-trips silently dropped
> `kind`/`vd_states`, demoting designed personas to clones. Fixed — community
> "preset" imports as `kind='design'` (a "voice" as `clone`), and bundles now
> carry `kind`+`vd_states` (old bundles import as clone). This makes the
> "accept only designed/synthetic" gate enforceable. Still to do: the consent
> attestation + AudioSeal-on-preview gate and the curation workflow below.

**Persona gallery — the territory is genuinely unoccupied.** The field splits into
consent-heavy commercial (ElevenLabs Voice Library: live-read Voice Captcha
verification, human review, sharing limited to professional clones), a consent-free
gray market (voice-models.com, ~28k RVC models), and read-only single-project
registries ([piper-voices](https://huggingface.co/rhasspy/piper-voices)' single
`voices.json` is the proven local-first pattern). **No OSS, consent-aware,
browse-preview-install voice gallery exists.** The build recipe: piper-voices-style
JSON index in a public git repo (checksums + preview URLs, payloads on HF) +
Obsidian-style PR curation + our existing `VoiceGallery.jsx` as the browser (it
already has community hooks: `useCommunityX`, `communitySubmitUrl`).

**Consent gates (legal floor, not nice-to-have):** Tennessee ELVIS reaches tools
whose "primary purpose" is unauthorized voice likeness — a gallery distributing
named-person clones is much closer to that line than a TTS engine; Illinois HB 4875
reaches distribution *facilitators*; EU Art 50 marking applies from 2026-08-02.
Gates: accept only **designed/synthetic voices** (#317 mapper personas) and
**self-recorded voices with a recorded consent statement** (spoken attestation, not
a checkbox — the Consumer Reports critique); AudioSeal watermark mandatory on
preview audio (it's already a direct dependency and now fully MIT incl. weights,
with a 16-bit payload — enough to carry a persona ID); PR-based human curation;
takedown via issue template propagating on index refresh. Honest note: none of this
stops a determined fork — the gates protect *the project* and set norms; the index
is the one chokepoint we actually control.

**Portable persona format:** no standard exists (the one attempt, vox-format, has
zero adoption; the de-facto reality is five incompatible engine-native formats).
Recommend a minimal **`.ovsvoice`** zip: manifest (schema version, engine + design
params for deterministic-mapper reproducibility, tags), optional reference audio +
transcript, `consent.json` (creation method + attestation + timestamp), SPDX-style
license tag, watermarked preview. Demand signal: voicebox's
[#138](https://github.com/jamiepine/voicebox/issues/138) (export profiles for
Piper/Home Assistant) — design the format so a Piper-ONNX export target can be added
later. **Ladder:** G1 `.ovsvoice` export/import (S–M, standalone value) → G2
community index + Gallery "Community" tab (M) → G3 in-app submission via prefilled
GitHub PR/issue — mirrors our bug-reporter pattern, no accounts (M) → G4
similarity-search/ratings/ONNX export (L, later).

### R4 — Model & env management, GPU compat matrix

**Environment management — what the field converged on** (licenses verified):
ComfyUI's one-shared-env model is the cautionary tale (conflict UIs, downgrade
blacklists, and pip-state-repair files *as product features*; its 2026 fix is uv +
whole-env lockfiles, not isolation). Pinokio (MIT) and StabilityMatrix (AGPL —
patterns only, no code) both landed on **one venv per app** — exactly our sidecar
architecture — then clawed disk back at the filesystem layer. LM Studio/Ollama
sidestep Python entirely with decoupled, hot-swappable native runtime packs — the
strongest pattern, but ours only if we ever ship prebuilt engine binaries.
Code-portable references: Pinokio, Ollama, llama.cpp, lms CLI (all MIT),
huggingface_hub/hf-xet (Apache-2.0), uv (MIT/Apache). **Not portable:**
StabilityMatrix (AGPL), ComfyUI + Manager + comfy-cli (all GPL-3.0).

**The torch-duplication math (measured 2026-06):** uv's global cache dedupes via
link mode — default **`clone` (CoW) on macOS *and* Linux, `hardlink` on Windows**;
same wheel across N venvs ≈ one copy on disk, *iff* cache and venvs share a
filesystem. But dedup is per-identical-wheel: our IndexTTS2 sidecar (torch 2.6.x)
vs parent (torch 2.8.0) shares nothing — the Windows cu128 torch wheel alone is
**~3.2 GiB** (measured), Linux ~0.83 GiB + multi-GB `nvidia-*` deps. Partial
consolation on Linux: `nvidia-*` packages dedupe independently wherever pinned
versions coincide across torch versions. Levers: keep `UV_CACHE_DIR` + sidecar
venvs on one filesystem; consider pinning `UV_LINK_MODE=hardlink` on Linux (reflink
degrades on ext4); and treat "align the sidecar's torch pin with the parent
whenever the engine permits" as the single biggest disk decision. Watch item:
PyTorch **wheel variants** (shipped experimental in 2.8, NVIDIA+Astral
collaboration) will eventually make `uv install torch` auto-select the right CUDA
build; uv already ships `--torch-backend=auto`.

**The compat matrix is two-dimensional** — `(torch version, CUDA wheel variant) →
supported sm_XX set`, published in
[pytorch RELEASE.md](https://github.com/pytorch/pytorch/blob/main/RELEASE.md):
Blackwell sm_120 needs **2.7.0 + cu128 or later**; from 2.8 the cu128+ wheels
dropped Maxwell/Pascal (Turing sm_75 is the floor; Pascal users must pin cu126
variants). Driver minimums: CUDA 12.x wheels ≥ 525, 13.x ≥ 580. Both failure
directions ("GPU too new" sm_120-on-cu126 and "GPU too old" sm_61-on-cu128) throw
the same lazy `no kernel image` error *after* `cuda.is_available()` returns True —
which is why preflight must check capability, not availability. The documented
antipattern is Ollama's silent CPU fallback
([their own #14258](https://github.com/ollama/ollama/issues/14258)); voicebox's
silent-fallback bootstrap is our other autopsy. Build: detect (capability via
torch, driver via NVML) → gate engine installs with a specific message ("this
engine's cu128 build needs Turing+; you have Pascal — installing the cu126 build
instead") → **loud persistent CPU-fallback banner**, never silent. We already have
probes to build on (`engine_env.py` compute-capability check — today it only gates
`torch.compile`; `hardware_probe.py`).

**Model management — the HF cache is the blessed single source of truth.** The hub
cache layout is now a [language-agnostic spec](https://huggingface.co/docs/hub/local-cache)
adopted by llama.cpp among others; blobs are content-addressed (LFS SHA-256 =
filename, so integrity is re-checkable offline), and the v1.x CLI ships exactly the
manager primitives a UI needs: `hf cache ls --filter "accessed>30d"`, `hf cache rm`,
`hf cache prune`, **`hf cache verify`**. Gotchas verified: Windows without
Developer Mode falls back to copy-per-snapshot (degraded dedup); concurrent
downloads are lock-protected (`.locks/`) but **deletion is not** — a delete UI over
a shared cache must handle delete-vs-reader races (fine on Linux fd semantics,
breaks on Windows); env vars are read at import time, so a Settings-controlled
cache path needs a restart. Offline/restricted: `HF_HUB_OFFLINE`,
`HF_HUB_ETAG_TIMEOUT` (falls back to cache on timeout), `HF_ENDPOINT` for mirrors —
hf-mirror.com is community-run, not HF-official, and its compatibility with the new
Xet/CAS download path is untested (escape hatch: `HF_HUB_DISABLE_XET=1`). Also:
`hf_transfer` is now fully deprecated (Xet is the default transfer path) —
consistent with our existing stack guidance.

**Ladder:** (a) uv link-mode + shared-cache audit, document the dedupe behavior +
sidecar pin-alignment policy — S; (b) in-app preflight compat gate (capability +
driver → engine × wheel-variant table) with specific errors + loud CPU banner — M;
(c) model manager UI over `scan_cache_dir()` (per-model disk usage, evict,
re-verify, mirror setting) — M; (d) LM-Studio-style decoupled runtime packs — L,
**not recommended now**: our sidecar architecture already decouples engines; revisit
only if we ship prebuilt binaries.

### Consolidated new actions

| # | Action | Mode | Effort | First rung |
|---|---|---|---|---|
| 15 | Agentic v1: provider recipe + pipecat smoke test against `:3900/v1` | Docs + test | S–M | §R1 v1 |
| 16 | Remote backend: URL setting + bearer token + Tailscale docs page | Build | M total | §R2 rungs 1–3 |
| 17 | Audiobook v1: EPUB ingest → chapterized m4b → ACX mastering | Build (+ port Apache-2.0 pieces) | M+S+S–M | §R3 A1–A3 |
| 18 | `.ovsvoice` portable persona export/import | Build | S–M | §R3 G1 |
| 19 | Engine preflight compat gate + loud CPU-fallback banner | Build | M | §R4 (b) |
| 20 | Model manager UI over the HF cache primitives | Build | M | §R4 (c) |
| 21 | OpenAPI hygiene pass under the shipped Scalar UI | Build | M incremental | §R2 rung 5 |
| 22 | Consent-locked voice profiles (prerequisite for §R1 v2/v3 and §R3 G2+) | Build | M | §R1 guardrail 2 |

---

## User sentiment & market positioning

*Researched 2026-06-11. Issue volumes are hand-clustered from title analysis (both
trackers use almost no labels): the 250 most recent open voicebox issues, and all 32
open + 80 recent closed pyvideotrans issues. Reddit data is partly secondhand
(reddit.com blocks direct fetching); flagged where so.*

### voicebox: what its users hit (~377 open / ~156 closed — maintainer drowning)

| Complaint theme | Volume | Representative | For us |
|---|---|---|---|
| CUDA/GPU bring-up failures (sm_120 "no kernel image", Pascal unsupported, AMD/Intel ignored, 2.4 GB CUDA re-downloads) | ~45–50 of 250; 4 of their top-10 most-commented ever | [#417](https://github.com/jamiepine/voicebox/issues/417), [#594](https://github.com/jamiepine/voicebox/issues/594), [#728](https://github.com/jamiepine/voicebox/issues/728), [#676](https://github.com/jamiepine/voicebox/issues/676) | **Warning + opportunity.** Endemic local-AI tax we share — but their Windows installers shipped with CUDA silently broken for months. GPU auto-detect with explicit per-arch errors is a real differentiator *if it holds on the edges* |
| Model download / offline failures (cached models phoning home, infinite offline retry, no mirror option) | ~25 of 250 | [#557](https://github.com/jamiepine/voicebox/issues/557), [#434](https://github.com/jamiepine/voicebox/issues/434), [#546](https://github.com/jamiepine/voicebox/issues/546) | **Opportunity.** Maps exactly to our uv-mirror + HF-token capabilities. A "local-first" app that breaks offline is a betrayed promise users notice loudly |
| Startup crashes / white screens / **no Linux binary at all** | ~30 of 250 | [#513](https://github.com/jamiepine/voicebox/issues/513), [#617](https://github.com/jamiepine/voicebox/issues/617), [#606](https://github.com/jamiepine/voicebox/issues/606) (regression of an earlier fix), [#682](https://github.com/jamiepine/voicebox/issues/682) | **Opportunity.** Regressions recur because fixes ship fast without cross-platform gates — our parity rule + 3-platform smoke matrix is precisely this gap |
| Generation quality bugs (30 s transcription cutoff, reference audio leaking into output, refinement silently translating to English) | ~25 of 250 | [#604](https://github.com/jamiepine/voicebox/issues/604), [#609](https://github.com/jamiepine/voicebox/issues/609), [#603](https://github.com/jamiepine/voicebox/issues/603) | **Warning.** Engine-level artifacts we inherit too; the fixable subset (silent language handling, truncation) are pipeline bugs — testable |
| Dictation/capture friction (Windows keyboard hooks, double auto-paste) | ~10 of 250 | [#687](https://github.com/jamiepine/voicebox/issues/687), [#697](https://github.com/jamiepine/voicebox/issues/697) | **Warning.** We shipped the same class of fix (#287/#299). OS-hook dictation is a permanent treadmill on all three platforms |
| Docs rage — their single most-reacted issue ever is a failed first run blamed on missing docs | top-reacted (17 reactions) | [#108](https://github.com/jamiepine/voicebox/issues/108); [#185](https://github.com/jamiepine/voicebox/issues/185) (32 comments, top open: fine-tune instructions) | **Opportunity.** This is literally our core value. The community reply in #108 — "this is open-source software, not free support" — is the failure mode we exist to avoid |

**Praise** (consistent across coverage): cloning quality ("near-perfect" from 3–5 s;
one reviewer scored it above ElevenLabs Multilingual v2 on cloning accuracy);
privacy + zero cost as the hook of every viral post; the Stories timeline editor and
MCP agent-voice as "genuinely innovative"; out-of-box Metal acceleration; maintainer
responsiveness — even as the backlog grows. Notably its 29.7k★ came from
X/Threads/LinkedIn/Reddit virality; it [barely registered on HN](https://news.ycombinator.com/item?id=47831411)
(1 point).

**Abandonment:** mostly switch-*backs* to ElevenLabs on Windows — the
[substack reviewer's verdict](https://theaitoolkit2.substack.com/p/i-tested-voicebox-the-free-local):
"Windows users should wait weeks for GPU fixes. Low-volume creators should stick
with ElevenLabs' $5/month simplicity." Plus churn-risk from unstable main
([#648](https://github.com/jamiepine/voicebox/issues/648)) and a reputational drag:
[TechTimes covered](https://www.techtimes.com/articles/316850/20260519/voicebox-clones-any-voice-3-seconds-audio-runs-locally-free-has-no-consent-lock.htm)
voicebox having "no consent lock" amid voice-fraud concerns — an angle where our
AudioSeal default is the counter-story.

**Most-engaged requests:** fine-tune instructions (top open, 32 comments); export
voice profiles to ONNX for Piper/Home Assistant
([#138](https://github.com/jamiepine/voicebox/issues/138) — a self-hosting crowd
signal); SenseVoice/FunASR STT requested **five separate times in one week**; AMD
DirectML; Linux support.

### pyvideotrans: what its users hit (32 open / ~887 closed — aggressive solo triage)

| Complaint theme | Volume | Representative | For us |
|---|---|---|---|
| Pipeline hangs + faster-whisper GPU teardown crashes (long-standing per users) | recurring across versions | [#1129](https://github.com/jianchang512/pyvideotrans/issues/1129), [#1118](https://github.com/jianchang512/pyvideotrans/issues/1118) | **Warning.** Upstream faster-whisper lifecycle bugs — we run the same stack. User-found mitigations (pre-segment audio, CPU fallback for long audio) are worth implementing as automatic fallbacks; Spec 7 contains the blast radius |
| Subtitle/audio sync drift, silence-removal eating final words, merged batch translations | ~10 open + the most-reacted closed bugs | [#923](https://github.com/jianchang512/pyvideotrans/issues/923) (22 comments), [#1012](https://github.com/jianchang512/pyvideotrans/issues/1012) | **Warning (endemic to dubbing).** Speech-rate mismatch is the hardest unsolved problem in the category — anyone evaluating our dub pipeline judges us on exactly this. Specs 1 + 5 are the answer |
| External TTS engine integration breakage (GPT-SoVITS, F5-TTS, index-tts break at the API seam) | ~10 open | [#636](https://github.com/jianchang512/pyvideotrans/issues/636) (26 comments), [#954](https://github.com/jianchang512/pyvideotrans/issues/954) | **Opportunity.** They delegate TTS to a zoo of self-hosted side-servers; every seam is a support ticket. Our bundled-engine model removes this entire class — a concrete pitch. (Their broken `_omnivoice.py` is this same theme pointed at us — Spec 11) |
| Install failures, especially macOS (source-only; Windows gets a praised .exe) | ~6 recent cluster; all-time #2 most-commented issue is literally "Installation tutorial" ([#193](https://github.com/jianchang512/pyvideotrans/issues/193), 67 comments) | [#950](https://github.com/jianchang512/pyvideotrans/issues/950), [#952](https://github.com/jianchang512/pyvideotrans/issues/952) | **Opportunity.** macOS/Linux users are second-class there; a signed mac installer with a working first run is a direct wedge |
| CUDA errors (GPU fails, silently falls back to CPU) | steady trickle; [#287](https://github.com/jianchang512/pyvideotrans/issues/287) 22 comments | [#177](https://github.com/jianchang512/pyvideotrans/issues/177), [#980](https://github.com/jianchang512/pyvideotrans/issues/980) | **Warning** — though notably smaller than voicebox's, because they treat CPU as the default path and GPU as opt-in |
| LLM translation plumbing (thinking-tags leaking into subtitles, stripped punctuation, merged lines) | ~8 | [#921](https://github.com/jianchang512/pyvideotrans/issues/921), [#979](https://github.com/jianchang512/pyvideotrans/issues/979) | **Opportunity (partial).** LLM-output sanitization is cheap, testable hygiene that visibly differentiates output quality |
| **Their VoiceStudio integration is reported broken by users** | open | [#1124](https://github.com/jianchang512/pyvideotrans/issues/1124) | Confirms the Spec 11 finding from their side of the bridge |

**Praise:** the packaged Windows .exe needing zero Python setup; completely free
with no login/registration/gates; breadth of integrations; responsive maintainer.
The [Aug 2024 HN thread (182 points)](https://news.ycombinator.com/item?id=41234713)
praised the democratization angle — dubbing for material nobody would pay a human
to dub. **Abandonment:** toward subtitles-over-dubbing entirely, Yandex Browser /
YouTube auto-dub for casual use, paid dubbing (ElevenLabs, DeepDub) when emotional
fidelity matters, and VideoLingo within the OSS niche for subtitle quality.
**Most-engaged requests:** model-chasing (SenseVoice — same ask hit voicebox 5× the
same month; index-TTS v2; Fish Audio; Qwen3-TTS), automatic per-speaker role
assignment, emotion transfer into dubs, a manual subtitle-proofread checkpoint
before merge, per-line audio export.

### Comparative read

- **voicebox↔pyvideotrans comparisons are essentially absent** — they own different
  frames (English-social "ElevenLabs alternative" vs Chinese-ecosystem "video
  translation pipeline"). **We are unusual in straddling both**, which is a
  positioning asset nobody currently contests.
- **vs ElevenLabs, what tips the decision:** toward local — cost at volume and
  privacy, every time; back toward cloud — (1) first-run failure (especially
  Windows GPU), (2) raw quality ceiling / emotional fidelity, (3) "just works"
  simplicity for low-volume users. Notably, *quality is no longer the automatic
  cloud win* (voicebox cloning reviews beat ElevenLabs Multilingual v2) — the
  deciding factor has shifted to **reliability of install and GPU bring-up**, i.e.
  exactly our stated core value.
- **The category wishlist** (duplicated across both trackers): SenseVoice/FunASR
  ASR, index-TTS v2, Fish Audio, per-speaker dubbing, emotion control, fine-tuning
  instructions.

### ElevenLabs pricing pressure (verified on [elevenlabs.io/pricing](https://elevenlabs.io/pricing), 2026-06-11)

Free $0 / 10k credits (no commercial license, no cloning) · Starter $6/mo / instant
cloning · Creator $22/mo / professional cloning · Pro $99 · Scale $299 (3 pro
clones) · Business $990 (10 pro clones). The complaints that push users local:

- **Dubbing multiplies cost per target language** — a 10-min video into 3 languages
  bills as 30 minutes; Creator includes ~50 dubbing minutes with $0.60/min overage
  ([their help article](https://help.elevenlabs.io/hc/en-us/articles/23338815703697-How-much-does-Dubbing-cost)).
  This is exactly the multi-language batch workload where local-and-free is most
  compelling — and our 50-video batch users' workload.
- **Editing a dub costs credits** — regenerating a clip bills each time; the credit
  rebate covers roughly one full re-dub
  ([dubbing studio docs](https://elevenlabs.io/docs/dubbing/studio)). They built
  per-segment regeneration *and had to bolt a rebate scheme onto it* — direct
  evidence the incremental-re-dub pain is real and monetized against.
- **Cloning is paywalled** at every tier boundary; commercial use paywalled on Free.
- **Credits-as-abstraction** generates its own churn-intent search ecosystem
  (third-party "what do credits actually cost" explainers).

### Honest verdicts on our unique five

| Differentiator | Verdict | Evidence |
|---|---|---|
| Incremental re-dub | **Strong lever, unmarketed** | ElevenLabs monetizes against this exact pain (rebate scheme). Nobody *searches* the term — show it (demo GIF), don't name it |
| 646 languages | **Strong reach/press lever; niche for retention** | It's the hook in all existing coverage of us; Chatterbox's English-only limit draws recurring complaints. Caveat: most users need 1–3 languages, and no third party has verified long-tail quality — overselling invites "language #412 sounds terrible" backlash |
| AudioSeal watermarking | **Nobody-asked (users) / press + compliance asset** | Zero end-user search demand (hobbyists prefer unwatermarked); but reviewers spontaneously praise it, roundups list missing watermarking as an open-source *limitation*, 2026 disclosure regulation makes it a compliance story, and voicebox's "no consent lock" press is the counter-example. Frame as the commercial-use objection-killer; don't lead with it |
| Cross-platform dictation default | **Strong lever, especially Linux** | The WisprFlow-alternative market is crowded on macOS, thin on Windows, and served only by single-purpose tools on Linux (active 2026 development = live demand). Nobody offers dictation+cloning+dubbing in one cross-platform app. Caveat: dictation searchers want a small focused app — message it as "already on your machine", not as a lightweight utility it isn't |
| 3-step translation chain | **Niche; users value the outcome, never the mechanism** | Real evidence that LLM translation quality matters (pyvideotrans documents it beats Google/DeepL; context-aware modes sell subtitle tools). Nobody searches "3-step chain" — market as "translations that don't sound like Google Translate" with a before/after; bury the architecture in docs. Note VideoLingo ships the same idea — it's a parity feature inside the niche, a differentiator outside it |

### Our own footprint (2026-06-11)

- Repo: 6,808★ / 1,045 forks, created 2026-04-09 — strong two-month trajectory.
- Press: [MarkTechPost](https://www.marktechpost.com/2026/05/26/meet-omnivoice-studio-a-local-open-source-alternative-to-elevenlabs/)
  (accurate, positive) and an uncritical blog endorsement; a viral X post framing
  us as killing "$700/year in ElevenLabs and HeyGen subscriptions" — note it
  positions *dubbing* as the hero feature. No independent quality review of the
  646-language claim exists yet — **our most exposed flank**: expectations are
  being set high with no third-party validation behind them.
- **Hacker News: effectively absent.** No submission with traction; every
  comparable tool got its bump there. An unclaimed opportunity.
- **Name collision (the big misconception risk):** three entities share "VoiceStudio"
  — (1) the k2-fsa VoiceStudio *model* (our default engine; their community-projects
  page lists us, underselling us as "desktop application for voice generation");
  (2) omnivoice.app, an unrelated commercial cloud product; (3) us. Most "VoiceStudio"
  YouTube traffic and the pyvideotrans VoiceStudio docs page are about the *model* —
  search demand is being split three ways and both competitor trackers contain
  "support VoiceStudio" requests that mean the model, not us.

### Positioning moves

1. **"vs ElevenLabs dubbing cost" comparison page** anchored on the multiplier math
   (10 min × 3 languages = 30 billed minutes; $0.60/min overage; pay-to-edit), with
   one table: "20-min video → 3 languages → fix 5 lines → re-export" priced on
   Creator vs $0 local. Natural home for the incremental-re-dub demo (30 s GIF:
   edit one line → only that segment regenerates). Targets the highest-intent query
   cluster ("elevenlabs pricing/dubbing cost") that third parties currently
   monetize.
2. **Show HN, leading with the install story, not the model.** HN's documented
   objections to local TTS are install friction and English-only — our installer +
   GPU auto-detect + 646 languages answer both. Title shape: "Show HN: Local
   ElevenLabs alternative — dub, clone, dictate on your own GPU, one installer."
3. **Claim the name before the collision hardens:** README/FAQ disambiguation
   ("VoiceStudio **Studio**, the desktop app built on the k2-fsa VoiceStudio engine —
   not omnivoice.app"); ask k2-fsa to upgrade our one-line community listing to
   mention dubbing/dictation; get the pyvideotrans VoiceStudio docs page pointing at
   Studio as the GUI path (pairs with Spec 11 — arrive with the fixed integration).
4. **A discoverable dictation entry point:** a docs/landing section "open-source
   WisprFlow alternative for Mac, Windows, and Linux (built into VoiceStudio
   Studio)" + PRs to the alternative-list aggregators. Proven, high-conversion
   query pool; no incumbent covers Linux well.

---

## Appendix: engine evaluation — ResembleAI Chatterbox (2026-06-11)

**Verdict: integrate later — not now.** Full facts verified against the HF cards,
GitHub pyproject, and PyPI (0.1.7, 2026-03-26).

- **License: clean.** MIT on code *and* all three weight variants (original 0.5B EN,
  Multilingual 23-lang, Turbo 350M) — compatible with our AGPL + commercial
  dual-license. The "Resemble uses special weight terms" worry did not materialize.
- **What it would add:** Turbo's inline paralinguistic tags (`[laugh]`, `[cough]`,
  `[chuckle]`) and the single-knob `exaggeration` expressiveness control — genuinely
  unique in our roster. Fast English cloning (cloning + speed is a gap; KittenTTS is
  fast but can't clone). The 23-lang multilingual cloning is **not** differentiating
  for us (VoiceStudio 646, VoxCPM2 30 @ 48 kHz).
- **Why not now:**
  1. `chatterbox-tts` hard-pins `torch==2.6.0` + `transformers==5.2.0`; we constrain
     `torch==2.8.0` and require `transformers>=5.3.0` — **unresolvable in the parent
     venv**, forcing a dedicated-venv sidecar (IndexTTS2 pattern, ~800–1000 LOC) that
     downloads a *second multi-GB torch*. The disk/download cost is the price, not
     the code.
  2. `resemble-perth` (its built-in PerTh watermarker) is a **git-URL dependency** —
     unmirrorable on restricted networks, against our bootstrap story. Also untested
     interaction: PerTh + our AudioSeal = double watermarking.
  3. MPS is buggy upstream (float64 conversion crash on Turbo; placeholder-storage
     errors); honest Apple-Silicon support means carrying community patches. Mac-ARM
     users already get Chatterbox today via our MLX-Audio curated list
     (`mlx-community/Chatterbox-TTS-4bit`).
- **Cheapest path / re-eval triggers:** ResembleAI publishes official
  [chatterbox-turbo-ONNX](https://huggingface.co/ResembleAI/chatterbox-turbo-ONNX)
  exports. If a 1-day spike proves it runs on plain `onnxruntime`, Turbo slots into
  the lightweight supertonic3-style sidecar (~700 LOC, **no second torch**) and we
  get the paralinguistic tags cheaply. Also re-evaluate if upstream relaxes the
  torch/transformers pins or publishes `resemble-perth` to PyPI.
