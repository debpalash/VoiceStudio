# probe — spec-driven test harness

`probe` is a portable test harness for VoiceStudio (and, by design, other
projects/APIs). It is **mostly deterministic with three narrowly-scoped agent
roles**, not an "AI does everything" system — that distinction is the whole point.

## The one principle: separate the Actor from the Judge

```
ACTOR  (AI agent / HTTP call / browser)  ──drives──▶  app under test
                                                          │ produces artifacts
                                                          ▼
JUDGE  (deterministic code + metrics)    ◀──renders the verdict
```

- The **Actor** may be flexible, self-healing, non-deterministic.
- The **Judge** is deterministic code + objective metrics only. **No LLM ever
  sits on the verdict path** — except a clearly-labelled, non-blocking
  `advisory` lane. Letting an agent both *act* and *judge* produces false passes
  (green tests on broken software), which is worse than no test.

## Feature coverage (specs)

Beyond the layer skeleton, the suite covers these features — one `*.probe.yaml`
spec each, run against the real app where possible (a single subprocess boot is
shared across the backend-touching specs):

| Spec | Layer | Verifies |
|------|-------|----------|
| `first_run` | env | fresh-dir boot: health + DB init + endpoints |
| `migration` | env | alembic UPGRADE on existing `omnivoice_data` fixture (backward-compat) |
| `engines` | engine | TTS/ASR registry: active engine available, every unavailable engine explains why (11 TTS / 7 ASR backends) |
| `security` | security | system routes reject non-loopback origins (403) |
| `dictation` | dictation | streaming-ASR WebSocket `/ws/transcribe` registered + accepts loopback handshake |
| `tts_smoke` / `voice_clone` / `voice_design` | media | audio-correctness ladder (decode/duration/not-silent/clipping/WER) + speaker-sim (clone) |
| `dub_export` | dubbing | segment duration-ratio, SRT/VTT well-formed, export-archive contents, output language-ID (advisory) |
| `i18n_parity` | i18n | locale files valid JSON (gate); orphan-keys + coverage (advisory) |
| `desktop_smoke` | desktop | Tauri config integrity (version parity, dev/build wiring, bundled bins, CSP) |
| `launchpad` | web | UI render via Playwright Driver (FakePage offline) |
| `coverage_critic` | meta | every declared layer still has a spec (drift gate) + API-surface inventory (advisory) |

> The i18n orphan-key check **surfaced a real bug**: all 20 non-`en` locales carry
> `gallery.cat_*` (and some `bootstrap.lines`) keys absent from the `en`
> reference. It's reported in the advisory lane (non-blocking) rather than gating,
> since the fix is a product change.

## Layers

| Layer | Module | Status | What it does |
|-------|--------|--------|--------------|
| L1 API fuzz | `api_fuzz.py` / `test_api_fuzz.py` | ✅ wired, enable-on-demand | Schemathesis property-fuzzes the FastAPI app in-process over ASGI for 500s / schema violations. |
| L2 Web UI | `web.py` · `judges/web.py` · `test_probe_web.py` | ✅ built (live = enable-on-demand) | Playwright Driver + deterministic self-heal + judges. Self-heal logic + judges are unit-tested offline against a FakePage; the live browser skips without Playwright/frontend. |
| L3 Desktop | `desktop.py` · `judges/desktop.py` · `test_probe_desktop.py` | ✅ built + tested | Tauri **config-integrity** (version parity vs pyproject, dev/build wiring, bundled `uv`/`ffmpeg` binaries, CSP permits the local backend — a desktop-only failure mode) against the real `tauri.conf.json` incl. platform-override merge. Plus a guarded live bundle launch (skips without a built bundle/display). Tauri macOS has no official WebDriver — backend-over-HTTP (L5) + browser (L2) substitute for E2E, per the architecture decision. |
| L4 Media | `judges/` | ✅ built + tested | Audio **correctness** verification: exists/decodes/duration/not-silent/not-clipping/no-NaN, round-trip ASR WER, speaker similarity. |
| L5 Env / first-run | `env.py` · `_boot_runner.py` · `test_probe_env.py` | ✅ built + tested | Fresh-data-dir backend boot **in a subprocess** (no session contamination); asserts health, DB init, endpoint reachability. Docker boot gated behind a daemon check. |
| Triager | `triage.py` · `test_triage.py` | ✅ built + tested | Clusters/dedupes blocking failures, sanitizes (home paths + tokens), and drafts a **prefilled GitHub issue URL** (no auto-submit, no credential). The HTML report shows a one-click "Draft GitHub issue" button when a run fails. |

## The hybrid spec format

Simple tests are declarative YAML (`specs/*.probe.yaml`); anything the schema
can't express drops to a plain pytest function calling the same judge functions
(the escape hatch). See `specs/tts_smoke.probe.yaml`. A spec separates:

- `steps` — what the **Actor** does (executed per-layer; captures artifacts into
  the run context as `$.name`).
- `judge` — deterministic **blocking** verdicts. A failure fails the test.
- `advisory` — **non-blocking** metrics (naturalness predictors, trends). Never
  gate; reported only.

```python
from tests.probe import load_spec, run_judges, blocking_failures   # (relative within the probe pkg)
spec = load_spec("specs/tts_smoke.probe.yaml")
results = run_judges(spec, context={"audio": out_path},
                     backends={"transcriber": FasterWhisperTranscriber()})
assert not blocking_failures(results)
```

## Running

```bash
uv run pytest tests/probe -q          # judges + spec engine (offline, no models)
```

The harness's own tests use synthetic audio + a `FakeTranscriber`, so they run
in milliseconds with no GPU and no model downloads. Real verification injects
the live backends (`FasterWhisperTranscriber`, a Resemblyzer/ECAPA embedder).

## HTML report

Every probe session writes a **self-contained HTML report** (inline CSS+JS, no
external assets) to `tests/probe/reports/`:

```
tests/probe/reports/report-YYYYMMDD-HHMMSS.html   # this run
tests/probe/reports/report-latest.html            # stable pointer to the newest
```

The report shows the verdict (blocking failures only), summary cards
(passed / failed / skipped / advisory), per-spec tables with status badges and
measured values, filter buttons, and the honest-ceiling note. The `advisory`
lane and `SKIP`s are visually separated and never affect the verdict.

Set `PROBE_OPEN=1` to open the report in your browser when the run ends; it
never opens otherwise (and never in CI or with `PROBE_NO_OPEN=1`). Override the
output location with `PROBE_REPORT_DIR`.

Tests feed the report via the session-scoped `probe_report` fixture:

```python
def test_something(probe_report):
    results = run_judges(spec, context={...}, backends={...})
    probe_report.record(spec, results)        # → a row group in the report
```

Render programmatically without pytest:

```python
from tests.probe.report import Report, SpecOutcome, save_and_open
save_and_open(Report(outcomes=[SpecOutcome.from_spec(spec, results)]))
```

### Enabling the heavier layers

```bash
uv add schemathesis                          # L1 API fuzzing (skips until then)
uv add resemblyzer                           # L4 speaker-similarity (skips until then)
uv add playwright && uv run playwright install chromium   # L2 live browser
uv add anthropic        # L2 agentic self-heal (LLMHealer); set ANTHROPIC_API_KEY
# faster-whisper + whisperx are already in the base venv (round-trip ASR works now)
# L5 Docker boot activates automatically when a Docker daemon is reachable.
```

### What runs offline vs. enable-on-demand

| Runs now (base venv, no models/GPU) | Skips until enabled |
|---|---|
| L4 judges + spec engine + report | L1 fuzz (needs `schemathesis`) |
| L5 first-run boot (subprocess, model short-circuited) | L4 speaker-sim (needs `resemblyzer`) |
| L2 self-heal logic + judges (FakePage) | L2 live browser (needs Playwright + `bun run dev`) |
| | L5 Docker boot (needs a daemon) |

L2's agentic self-heal escalates: primary selector → deterministic fallback
candidates (id→test-id→text, loosened CSS) → pluggable `Healer`. The default is
`NoopHealer` (deterministic). For genuine agentic self-heal, pass
`launch(healer=anthropic_healer())` — `LLMHealer` asks a model to propose a
selector from the live page HTML. It's provider-agnostic (`LLMHealer(complete_fn)`)
and unit-tested offline with an injected completion. The verdict always comes
from the deterministic judges, never the Driver or the model.

## The honest ceiling — read this before trusting a green dashboard

`probe` verifies that output is **correct and not broken**. It does **not**
verify that output is **good**.

- ✅ Trustworthy autonomously (~70–80% of features): crashes, 500s, schema
  breaks, silent/gibberish/wrong-language audio, truncation, duration drift,
  broken installs, locator drift.
- ❌ **Human-judgment-only (~10–15%):** naturalness, prosody, emotional
  appropriateness, accent authenticity, "sounds like a convincing me",
  "the UI feels right". The metrics that *claim* to score these (MOS predictors
  like UTMOS/NISQA/SQUIM) fail out-of-domain, and most of 646 languages is
  out-of-domain — so they live in the `advisory` lane and **never gate**.

Design rules that enforce this, baked into the code:

- **No golden-WAV fixtures.** PyTorch is non-reproducible CPU-vs-GPU even with
  fixed seeds; a byte compare would manufacture platform-only regressions
  (a P0 violation of the cross-platform-parity rule). Gate on *metrics*, not
  waveforms.
- **WER gates at ~0.10–0.15, never 0** — it measures your TTS *plus* the ASR's
  own errors. A *rising* WER on a fixed sentence beats any absolute number.
- **Speaker similarity is a relative gate** — calibrate per-engine, alert on
  drops; the default English-biased encoder is unreliable for other languages.
- **NISQA's weights are CC-BY-NC-SA (non-commercial)** — do not bundle it in a
  shipped build; prefer UTMOS / TorchAudio-SQUIM / TTSDS2.

A harness that hides what it can't verify is worse than nothing. `probe` reports
skips and advisories explicitly so a green run never overstates its confidence.
