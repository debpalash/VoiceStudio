# Spec 01 — Expressive TTS: emotion/style direction + pronunciation control

**Date:** 2026-06-25
**Status:** Proposed
**Target line:** v0.3.x (continuous-to-main; no RC, no deferral to v0.4)
**Related:** `docs/specs/2026-06-12-elevenlabs-parity-program.md` (this is the "perceived-quality" half that program left open), issues #674/#679 (design-mode profile_id handling).

---

## 1. Context & Problem (gap vs ElevenLabs)

The #1 perceived-quality gap users report vs ElevenLabs is **expressive delivery**. ElevenLabs v3 ships two things we don't expose coherently:

1. **Audio tags** — inline square-bracket performance cues (`[excited]`, `[whispers]`, `[sigh]`, `[laughs]`) that steer emotion/tone *mid-line*, plus situational/reaction tags ([ElevenLabs v3 audio tags](https://elevenlabs.io/blog/eleven-v3-audio-tags-expressing-emotional-context-in-speech), [help: how audio tags work](https://help.elevenlabs.io/hc/en-us/articles/35869142561297-How-do-audio-tags-work-with-Eleven-v3)).
2. **Pronunciation dictionaries** — per-term rules with **phoneme** (IPA/CMU via SSML `<phoneme>`) and **alias** (respelling) entries, checked start-to-end, first match wins, case-sensitive ([ElevenLabs pronunciation dictionaries](https://elevenlabs.io/docs/eleven-api/guides/how-to/text-to-speech/pronunciation-dictionaries)).

**What we already have (and must reuse, not reinvent):**

- `backend/services/ssml_lite.py` — inline `[slow]/[fast]/[emphasis]/[spell]` tags → ordered prosody segments `{text, speed, spell, emphasis}`. ReDoS-safe fixed-alternation regex. Already wired into `longform_parser.py`.
- `backend/services/pronunciation.py` — `apply_lexicon(text, {term: respelling})`: whole-word, case-insensitive, longest-key-first, word-boundary aware, ReDoS-safe single-pass `re.sub`. **This is the alias-rule engine; it has no DB persistence and no IPA path yet.** Used today only by audiobook (`services/audiobook.py:119`, `api/routers/audiobook.py`).
- `backend/services/longform_parser.py` — the canonical grammar: precedence `# chapter → [voice:NAME] → [pause] → SSML-lite → [spell]`. JS twin `frontend/src/utils/longformParser.js`, golden corpus `tests/fixtures/longform_parser_cases.json`. **This is where multi-voice `[voice:NAME]` story tags live — our new emotion tags must not collide with it.**
- `backend/services/chunked_tts.py` — sentence-boundary splitter that already **refuses to cut inside `[...]` bracket tags** (`_BRACKET_TAG_RE`, line 43). New tags inherit that protection for free.
- `omnivoice.utils.text.parse_pause_markers` — `[pause Nms]` span splitter, consumed in `generation.py:224`.

**What's missing / the gap:**

- No way to direct **emotion** at all from the Studio generate path. The only "style" the **VoiceStudio base model** accepts is the validated `instruct` taxonomy (Gender/Age/Pitch/**Style=whisper only**/Accent/Dialect) — confirmed in `core/describe_voice.py` and the engine's `omnivoice/utils/voice_design.py` validator. The base model **does not** take `[happy]`/`[sad]` ([k2-fsa/OmniVoice issue #78 — Emotion/Tone](https://github.com/k2-fsa/OmniVoice/issues/78)); only a finetune does.
- The capable engines express emotion **very differently**: CosyVoice 3 via natural-language instruct `…<|endofprompt|>` + inline `[laughter]`/`[breath]`/`<strong>` ([CosyVoice 3 paper](https://arxiv.org/html/2505.17589v1)); IndexTTS2 via an **8-dim emotion vector** `[happy,angry,sad,afraid,disgusted,melancholic,surprised,calm]` or an emotion-reference clip ([IndexTTS2](https://indextts.ai/), [arXiv 2506.21619](https://arxiv.org/html/2506.21619v2)); VoxCPM2 via a `(instruct)text` prefix (`tts_backend.py:423`).
- The lexicon is project-local JSON only — no global, no per-language, no DB persistence, no UI, no IPA/phoneme path, no inline one-off override.

**Design principle:** one engine-agnostic *intent* surface (tags + sliders + dictionary) that **lowers** to whatever each engine can actually do, and **degrades visibly** (never silently) where it can't.

---

## 2. Goals / Non-goals

**Goals**

- G1. Inline emotion/style tags in the generate text — `[excited]`, `[whispers]`, `[sad]`, `[shouting]`, `[laughs]`, … — parsed into per-span *expression intent*, composing cleanly with existing `[voice:]`/`[pause]`/SSML-lite/`[spell]`.
- G2. A non-inline alternative for users who don't want to learn tags: an **Expression** panel on the generate/Studio UI (emotion dropdown + intensity slider + optional **emotion-reference clip** picker) that sets a per-render default.
- G3. Per-engine **capability matrix** that maps expression intent → the engine's real mechanism, surfaced in the UI so users know what their selected engine will honor before they hit generate.
- G4. A user-editable **pronunciation dictionary** persisted in the DB (global + per-language scope), with **alias** (respelling) and **phoneme** (IPA/CMU) entry types, applied at synth time before the model, plus inline one-off overrides.
- G5. Backward-compatible: existing engines, on-disk profiles, the project-local audiobook lexicon JSON, and plain (tag-free) text all keep working byte-identically.
- G6. Local-first, identical default behavior on macOS/Windows/Linux.

**Non-goals**

- N1. Per-phoneme prosody curves / full SSML (`<prosody>`/`<break>` trees). SSML-lite + `[pause]` stay our prosody surface.
- N2. Training/finetuning an emotion model. We expose what shipped engines already do; the VoiceStudio base model's emotion ceiling is its instruct taxonomy, and we say so.
- N3. A learned text→emotion classifier in the base path (IndexTTS2's own T2E module is used when *that* engine is active; we don't build a global one).
- N4. Auto-generating IPA from spelling (no g2p engine bundled in this spec — see Open Questions Q4).

---

## 3. User Experience (UI + flows)

### 3.1 Inline expression tags (power path)

In the Studio / generate text box the user writes:

```
[excited] We did it! [pause 400ms] [whispers] ...but don't tell anyone.
```

- Tags are stripped from spoken text and turned into per-span intent.
- Tags compose with multi-voice stories: `[voice:Morgan] [angry] Get out. [voice:Sam] [nervous] O-okay.` — `[voice:]` switches narrator (existing), `[angry]`/`[nervous]` set that span's emotion.
- An **"⊕ Insert"** popover (reusing the existing clone-tab insert popover pattern, #672) lists available tags **filtered to what the active engine supports**, with a tooltip showing the lowering ("`[excited]` → CosyVoice instruct / IndexTTS2 emo-vector / VoiceStudio: not supported, ignored").
- Unsupported-on-this-engine tags render with a subtle strikethrough chip and a one-line banner: *"VoiceStudio ignores emotion tags — switch to CosyVoice 3 or IndexTTS2 for emotional delivery."* (never silent; mirrors the routing-banner convention from `engine_routing.py`).

### 3.2 Expression panel (no-tags path)

A collapsible **Expression** section under the generate controls:

- **Emotion** dropdown: Neutral (default) / Happy / Sad / Angry / Afraid / Surprised / Calm / Whisper / Shout. Maps to the engine's mechanism (sliders for IndexTTS2; instruct phrase for CosyVoice/VoxCPM; whisper-only for VoiceStudio).
- **Intensity** slider 0–100 (default 50). Only enabled when the active engine supports graded intensity (IndexTTS2 emo-vector magnitude); otherwise greyed with a tooltip.
- **Emotion reference** (optional): pick a short clip whose *delivery* (not timbre) is mimicked. Enabled only for engines with an emotion-ref path (IndexTTS2). This is **separate** from the voice-clone `ref_audio` — same control style, different slot.
- A live **"This engine will: …"** line shows the resolved lowering, so the panel doubles as the capability disclosure.

The panel sets request-level defaults; inline tags override per span (tag wins, same precedence rule as SSML-lite speed).

### 3.3 Pronunciation dictionary (Settings → Voice → Pronunciation)

A new **PronunciationPanel** (sibling of `VoicePanel.jsx`):

- A table of entries: **Term** | **Scope** (Global / language) | **Type** (Respelling / IPA / CMU) | **Replacement** | **Enabled**.
- Add/edit/delete rows; inline validation (IPA charset check; CMU ARPABET token check). Bad phoneme strings flagged before save, not at synth time.
- A **"Test"** field: type a sentence, see the post-substitution text (and, for phoneme rows, the `[[…]]` markup that will be handed to the engine) — no model call needed.
- Import/Export JSON (round-trips the existing audiobook lexicon shape, so a project lexicon can be promoted to global).

### 3.4 Inline one-off pronunciation override

Within text: `She lives on [[ˈnɛvʌdə]] street` (IPA in double brackets) or `[[Nuh-VAD-uh]]` (respelling). Applies once, overrides any dictionary entry for that occurrence. Chosen `[[…]]` because single `[...]` is already emotion/voice/pause tags — double brackets are unambiguous and don't collide.

---

## 4. Technical Design

### 4.1 Architecture overview

Two independent, composable layers, both **pure/CPU/stdlib** at the parse stage (model-free, unit-testable, cross-platform-identical):

```
text + request expression defaults
        │
        ▼
[A] expression parse  ── extends services/longform_parser.py grammar:
        # chapter → [voice:NAME] → [emotion] → [pause] → SSML-lite → [spell] → [[pronounce]]
        │            (new layer, slotted between voice and pause)
        ▼
spans: {voice_id, text, pause_ms_after, speed, expression}   ← expression added
        │
        ▼
[B] pronunciation apply  ── services/pronunciation.py (extended):
        apply_pronunciation(span.text, dict, language) → text with aliases substituted
        + [[…]] inline overrides resolved to engine phoneme markup or respelling
        │
        ▼
[C] expression lowering  ── services/expression.py (NEW):
        lower(expression, engine_id) → engine-specific kwargs
        (instruct phrase | emo_vector | emo_ref | whisper | <none>)
        │
        ▼
TTSBackend.generate(text, instruct=…, **expression_kwargs)
```

### 4.2 Files to add / extend (real paths)

**Add**

- `backend/services/expression.py` — the expression vocabulary + lowering. Pure, model-free. Defines:
  - `EXPRESSIONS` — canonical emotion set `{neutral, happy, sad, angry, afraid, surprised, calm, whisper, shout, laugh, sigh}` (the cross-engine intersection; superset of VoiceStudio's `whisper`).
  - `Expression` dataclass `{emotion: str, intensity: float, ref_audio: str|None}`.
  - `parse_expression_tags(text) -> list[(text, Expression|None)]` — splits a line on `[emotion]`/`[/emotion]` tags using the **same fixed-alternation, ReDoS-safe regex shape** as `ssml_lite.py` (`_TAG_RE`), tags drawn from `EXPRESSIONS`. Unknown bracket tokens are left **untouched** so they pass through to SSML-lite / `[voice:]` / `[pause]` — no grammar overlap.
  - `lower(expr, engine_id) -> dict` — the capability matrix in code (see 4.4). Returns kwargs to merge into `generate()`; returns `{}` + a `degraded` note for engines that can't honor it.
- `backend/api/routers/pronunciation.py` — CRUD for dictionary entries + `/pronunciation/test` (dry-run substitution, no model). Registered in `backend/main.py` alongside the other routers.
- `frontend/src/components/settings/PronunciationPanel.jsx` (+ `.css`, + `.test.jsx`).
- `frontend/src/utils/expressionTags.js` — JS twin of `parse_expression_tags` (mechanically mirrored, same golden corpus, exactly like `longformParser.js`).
- `backend/migrations/versions/0008_pronunciation_dictionary.py` — alembic migration (see §5.3).

**Extend**

- `backend/services/longform_parser.py::_parse_chapter_body` — insert the expression layer between `[voice:]` runs and the existing pause/SSML loop. Each emitted span gains an `expression` key (default `None`). The `Span` dataclass / `to_dict()` and the JS twin update in lockstep; `tests/fixtures/longform_parser_cases.json` gains expression cases.
- `backend/services/ssml_lite.py` — **no change to its grammar**; expression parsing runs as a sibling layer above it. `[whisper]` is handled by expression (engine-level), distinct from SSML-lite's prosody — documented so they don't drift.
- `backend/services/pronunciation.py` — add:
  - `apply_pronunciation(text, entries, language)` — alias substitution (delegates to existing `apply_lexicon` for respelling rows) **plus** phoneme rows lowered to the active engine's phoneme markup; per-language filtering (global rows always apply; language rows apply when `language` matches or is `Auto`).
  - `parse_inline_pronunciation(text, engine_id)` — resolve `[[…]]` overrides (IPA/CMU/respelling autodetected by charset) to engine markup or plain respelling, ReDoS-safe `\[\[[^\]]*\]\]`.
  - `load_dict_from_db()` / `save_dict_to_db()` — DB-backed counterpart to the existing JSON `load_lexicon`/`save_lexicon` (which stay for the audiobook project-local path).
- `backend/api/routers/generation.py::generate_speech` — accept `expression`, `expression_intensity`, `expression_ref` (Form fields) and a `pronounce: bool` toggle (default ON). Thread them through `_run_inference` / `_run_backend_inference` so the lowering kwargs reach `model.generate()` / `backend.generate()`. Pronunciation applied **per chunk before** `split_text_into_chunks`, mirroring `services/audiobook.py:124`.
- `backend/services/tts_backend.py` — extend the `generate(**extras)` contract with optional `emo_vector`, `emo_ref`, `emotion` kwargs. The ABC already takes `**extras`, so **no signature break**; each backend reads the kwargs it understands and ignores the rest (the existing graceful-degradation idiom, e.g. KittenTTS/MOSS at lines 510–527). Per-engine `generate()` bodies updated for CosyVoice (fold emotion into the `inference_instruct2` prompt), VoxCPM2 (`(instruct)` prefix), IndexTTS2 (`emo_vector`/`emo_ref` — its module in `engines/indextts/`).
- A new class attribute on `TTSBackend`: `expression_caps: dict` (e.g. `{"mode": "instruct"|"emo_vector"|"emo_ref"|"whisper_only"|"none", "emotions": [...]}`), defaulting to `{"mode":"none"}`. `list_backends()` surfaces it next to `gpu_compat` so the UI can filter tags/sliders per engine without a model load.

### 4.3 Data flow & composition (no collisions)

Grammar precedence (extends `longform_parser.py:10`):

```
# chapter  →  [voice:NAME]  →  [emotion]  →  [pause]  →  SSML-lite  →  [spell]  →  [[pronounce]]
```

- `[voice:NAME]` (existing, `_VOICE_RE`) is matched first → switches narrator. Emotion tags live **inside** a voice run, so `[voice:Sam]` and `[angry]` never compete for the same token.
- `[emotion]` tags are a **closed set** drawn from `EXPRESSIONS`; the regex only matches those literals, so a stray `[whatever]` is not consumed and flows to the lower layers (or out as literal text). This is the same closed-alternation safety `ssml_lite.py` relies on.
- `[pause Nms]`, SSML-lite, `[spell]` are unchanged and parse *within* an emotion span — emotion is an outer attribute, prosody/speed inner, exactly like the current voice→pause→ssml nesting.
- `[[pronounce]]` (double bracket) is resolved last, after tag stripping, so it can't be confused with single-bracket tags. `chunked_tts._BRACKET_TAG_RE` already protects single `[...]`; we widen it (or add a sibling) to also never split inside `[[...]]`.

**Tag-vs-text disambiguation rule (the load-bearing invariant):** a `[token]` is consumed by a layer **iff** `token` (case-insensitive) is in that layer's closed vocabulary (`EXPRESSIONS`, SSML-lite `_TAGS`, `voice:`-prefixed, or `pause …`). Everything else is literal. This is asserted by the shared golden corpus against both Python and JS parsers.

### 4.4 Per-engine capability matrix (`expression.lower`)

| Engine (`id`) | Mechanism | emotion | intensity | emo-ref | How `lower()` maps it |
|---|---|---|---|---|---|
| `omnivoice` (base) | `instruct` taxonomy, **Style=whisper only** | whisper only | no | no | `[whispers]` → append `whisper` to instruct (validator-safe via existing `heal_design_instruct`). All other emotions → `degraded`, banner shown. |
| `cosyvoice` | NL instruct `…<\|endofprompt\|>` + inline `[laughter]`/`[breath]`/`<strong>` | full set | coarse (phrasing) | no | emotion → instruct phrase ("speak in an excited tone") merged into `inference_instruct2`'s instruct arg (`tts_backend.py:846`). `[laughs]`/`[sigh]` → CosyVoice's own `[laughter]`/`[breath]` literals injected into text. |
| `indextts2` | 8-dim **emo_vector** + emo-ref + text-infer | full set | **yes (0–1)** | **yes** | emotion+intensity → one-hot-ish 8-vector scaled by intensity; emo-ref → `emo_audio` path. (`engines/indextts/`.) |
| `voxcpm2` | `(instruct)text` prefix | full set | coarse | no | emotion → `(speak excitedly)` prefix (`tts_backend.py:423`). |
| `kittentts`, `sherpa-onnx`, `gpt-sovits`, `mlx-audio`(varies), `moss-tts-nano`, `supertonic3` | none / preset | — | — | — | `lower()` → `{}` + `degraded`; tags stripped, text spoken neutrally. Banner: *"This engine has no emotion control."* |

`lower()` is the single source of truth for this table; `list_backends()['expression_caps']` is derived from the same constants so UI and synth never disagree (the `describe_voice.py` import-time-validation discipline — a missing/renamed capability fails loudly in a test, not at synth).

### 4.5 Pronunciation lowering

- **Respelling (alias) rows** → existing `apply_lexicon` (already correct: longest-first, boundary-aware, ReDoS-safe). Works on **every** engine — it's just text substitution.
- **Phoneme rows (IPA/CMU)** → engine phoneme markup where supported (e.g. CosyVoice/sherpa-onnx models with a phoneme front-end), else **graceful fallback to the respelling** if the row also has one, else passed through and flagged "phoneme not honored on this engine" (parity-rule: visible degradation). No engine is *broken* by a phoneme row; worst case it's spoken as the literal grapheme.
- Per-language: global rows always apply; language-tagged rows apply when request `language` matches (or is `Auto`). Matching resolves both sides through the bundled language map: picker names (`Spanish`) and ISO ids (`es`, `kbt`, regional `es-MX`) compare as the same language id.

---

## 5. API / Schema / Data-model changes

### 5.1 Endpoints

- `POST /generate` (extend, `generation.py`): new optional Form fields
  - `expression: str = Form("")` — emotion name or `""`/`neutral`.
  - `expression_intensity: float = Form(50, ge=0, le=100)`.
  - `expression_ref: UploadFile = File(None)` — emotion reference clip (engines that support it).
  - `pronounce: bool = Form(True)` — apply the pronunciation dictionary.
  Omitting all of them = byte-identical legacy behavior.
- `GET /pronunciation` → `[{id, term, scope, type, replacement, enabled, language}]`.
- `POST /pronunciation` / `PUT /pronunciation/{id}` / `DELETE /pronunciation/{id}` — CRUD with server-side IPA/CMU validation.
- `POST /pronunciation/test` → `{input} → {substituted, phoneme_markup}` (no model).
- `GET /engines/tts` (existing `list_backends`) gains `expression_caps` per entry. No new endpoint.

### 5.2 Prefs / settings

- New pref key `pronunciation_enabled` (default `true`) via `core/prefs.py` (`prefs.resolve`, env `OMNIVOICE_PRONUNCIATION` for power-users) — same pattern as `tts_backend`.
- Expression defaults are per-request, not persisted globally (a render-time choice, like `effect_preset`).

### 5.3 Alembic migration (additive, idempotent — sketch)

`backend/migrations/versions/0008_pronunciation_dictionary.py`, `down_revision="0007_rebuild_poisoned_design_instruct"`. Follows the 0004 idempotent-guard pattern exactly.

```python
revision = "0008_pronunciation_dictionary"
down_revision = "0007_rebuild_poisoned_design_instruct"

def _has_table(name):  # same helper as 0004
    bind = op.get_bind()
    return bind.execute(sa.text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name}).fetchone() is not None

def upgrade():
    if _has_table("pronunciation_entries"):
        return
    op.create_table(
        "pronunciation_entries",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("replacement", sa.Text(), nullable=False, server_default=""),
        sa.Column("type", sa.Text(), nullable=False, server_default="respelling"),  # respelling|ipa|cmu
        sa.Column("language", sa.Text(), nullable=False, server_default="*"),        # '*' = global
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Float(), nullable=True),
    )
    op.create_index("idx_pron_lang", "pronunciation_entries", ["language"])

def downgrade():
    if _has_table("pronunciation_entries"):
        op.drop_table("pronunciation_entries")
```

**Critically:** the same table must also be added to `core/db.py::_BASE_SCHEMA` as `CREATE TABLE IF NOT EXISTS pronunciation_entries (...)` so fresh installs and the `_reconcile_additive_columns` safety net converge on the identical end-state (the dual-path discipline already documented in `db.py:140`). The audiobook project-local JSON lexicon (`load_lexicon`/`save_lexicon`) is **untouched** — it remains the per-project override; DB entries are the global/default layer, merged dict-style (project JSON wins on key conflict, longest-first preserved).

---

## 6. Local-first & cross-platform compliance

- **No cloud.** Parsing, lexicon, and lowering are pure Python/stdlib + JS — zero network, zero model for the control plane. Emotion is realized entirely by the **already-on-device** engine the user selected; IPA/CMU validation is charset/table-based (no g2p service).
- **Default-parity (strict 2026-05-20 rule).** The *default* path — emotion tags, the dictionary, `[[…]]` overrides — behaves identically on macOS/Windows/Linux because it's pure text transformation guarded by the shared golden corpus run on both the Python and JS parsers. `longform_parser._normalize` already CRLF-normalizes, so Windows-authored scripts parse identically. **Engine-specific** emotion fidelity differs by engine (a property of the engine, not the platform) and is disclosed in the capability matrix UI — this is allowed because the *user-visible default behavior of the feature* (tags parse, dictionary applies, degradation is shown) is identical everywhere; only the opt-in *engine* changes what's honored.
- No platform-only tag or shortcut. The "⊕ Insert" popover and panels are the same component on every OS.
- Emotion-reference clip is processed on-device by the same engine path as voice refs; privacy unchanged (never uploaded, scrubbed from any bug report per CLAUDE.md capture rules).

---

## 7. Phasing (each slice lands independently on v0.3.x)

- **Phase 1 — Pronunciation dictionary (DB + UI).** Migration 0008 + `_BASE_SCHEMA` row + `pronunciation.py` extensions (`apply_pronunciation`, DB load/save, per-language) + `/pronunciation` router + `PronunciationPanel`. Wires into `/generate` (`pronounce` toggle) and reuses the existing audiobook apply-site. **Respelling rows only** in this phase (alias rules work on every engine). Ships value immediately, zero engine risk.
- **Phase 2 — Inline `[[pronounce]]` overrides + IPA/CMU rows.** Phoneme validation + engine-markup lowering for the engines that have a phoneme front-end; respelling fallback elsewhere. Widen `chunked_tts` bracket guard for `[[…]]`.
- **Phase 3 — Expression engine + lowering.** `services/expression.py` + `expression_caps` on backends + `lower()` for CosyVoice/VoxCPM2/IndexTTS2/VoiceStudio-whisper. `/generate` Form fields + `_run_*_inference` threading. No UI yet (API + tags usable headless/MCP).
- **Phase 4 — Inline emotion tags in the grammar.** Extend `longform_parser` + JS twin + golden corpus; `[emotion]` spans flow through audiobook/story/Studio. This is the collision-sensitive change, landed only after Phase 3's vocabulary is stable.
- **Phase 5 — Expression panel UI** (dropdown + intensity + emo-ref picker + "this engine will…" line + per-engine tag filtering in the Insert popover).

Phases 1–2 (pronunciation) and 3–5 (expression) are independent tracks; either can lead. Each phase = one PR through the review+security gate with its tests, bisectable, docs-synced.

---

## 8. Testing strategy (fail-before / pass-after)

**Pure-parser (no model, run in CI everywhere):**

- `tests/test_expression_parse.py` — `parse_expression_tags`: plain text → one neutral span (fail-before: function doesn't exist); nested `[voice:][excited]…[pause]…` precedence; unknown `[token]` passes through untouched (the anti-collision invariant); unclosed tag → applies to EOL; ReDoS corpus (long adversarial bracket runs) completes < 50 ms.
- Extend `tests/fixtures/longform_parser_cases.json` with expression cases; assert **byte-identical** output from `longform_parser.py` and `frontend/src/utils/expressionTags.js`/`longformParser.js` (the existing twin-parity gate). Fail-before: JS twin missing emotion key.
- `tests/test_pronunciation.py` — `apply_pronunciation`: per-language filtering (global applies, mismatched-language skipped, `Auto` applies all); respelling delegation matches legacy `apply_lexicon`; `[[ipa]]` inline override resolves; phoneme-on-unsupported-engine falls back to respelling and sets `degraded`. Idempotency (apply twice == once).
- `tests/test_expression_lowering.py` — `lower(expr, engine)` for every registered engine returns the matrix's expected kwargs; `expression_caps` on each backend matches what `lower()` actually consumes (the describe_voice-style import-time consistency assert, so a renamed cap fails a test, not synth).

**API:**

- `tests/test_pronunciation_api.py` — CRUD round-trip; IPA/CMU validation rejects garbage with 400; `/pronunciation/test` returns substituted text with no model loaded.
- `tests/test_generate_expression.py` — `/generate` with `expression=excited` on VoiceStudio returns 200 + an `X-VoiceStudio-Expression: degraded` header (visible degradation, never silent); on a mock CosyVoice backend, asserts the instruct phrase was injected.

**Migration:**

- `tests/test_migration_0008.py` — upgrade on a 0007-stamped DB creates the table; re-running is a no-op (idempotent guard); a fresh `_BASE_SCHEMA` DB and a migrated DB have **identical** `PRAGMA table_info(pronunciation_entries)` (the dual-path convergence assert, like the existing `_reconcile_additive_columns` tests). Backward-compat: an existing `omnivoice_data/` DB with no table upgrades cleanly and old rows untouched.

**Cross-platform / CI-green:**

- Twin-parity test covers Win/mac/Linux line endings via `_normalize`.
- No new Python runtime dep (Phase 1–4 are stdlib); no `frontend/package.json` change unless the panel pulls a new lib (it shouldn't) — if it does, regenerate root `bun.lock` and assert `bun install --frozen-lockfile` per the keep-main-green rule.

---

## 9. Risks & mitigations

- **R1 — tag/grammar collision** (emotion tag eats a `[voice:]` or a literal `[bracketed]` word). *Mitigation:* closed-vocabulary matching + the shared golden corpus asserting passthrough of unknown tokens against both parsers. This is the single highest-risk change → isolated to Phase 4, after the vocab is frozen.
- **R2 — silent degradation** (user picks `[excited]` on VoiceStudio, hears neutral, blames us). *Mitigation:* strikethrough chips, banner, and an `X-VoiceStudio-Expression: degraded` response header — parity with the no-silent-CPU-fallback rule. Capability matrix shown *before* generate.
- **R3 — IPA/CMU garbage → engine crash.** *Mitigation:* validate on save (400), fall back to respelling/literal at synth, never pass unvalidated phoneme strings to a model. Worst case = spoken as written.
- **R4 — IndexTTS2 emo-vector API drift** (it's subprocess-isolated, own venv). *Mitigation:* lowering for IndexTTS2 lives behind its `engines/indextts/` adapter; a contract test pins the kwarg names; if the kwarg is absent the adapter degrades to neutral (existing `**extras` ignore idiom).
- **R5 — lexicon/dictionary double-apply** (project JSON + DB). *Mitigation:* single merge point, project-wins ordering, idempotency test; respelling pass is already idempotent by construction (`pronunciation.py` single-pass `re.sub`).
- **R6 — DB migration on a preview-stamped DB** (alembic_version at a removed rev). *Mitigation:* the `_BASE_SCHEMA` + `_reconcile_additive_columns` belt already covers this class (`db.py`); the convergence test asserts it.

---

## 10. Open questions / decisions for the owner

- Q1. **Tag vocabulary:** adopt ElevenLabs' exact tag names (`[excited]`, `[whispers]`, `[sighs]`) for muscle-memory parity, or a neutral set? (Recommendation: alias the common ElevenLabs names to our canonical set so pasted ElevenLabs scripts "just work.")
- Q2. **Reaction tags** (`[laughs]`, `[sigh]`, `[gasp]`): treat as emotion spans, or as literal text injected for engines that support them (CosyVoice `[laughter]`/`[breath]`)? (Recommendation: a third tag class "sound" lowered per-engine; out of scope for Phase 3, note for later.)
- Q3. **VoiceStudio base-model emotion:** ship as whisper-only with honest degradation (this spec), or also wire the `ModelsLab/omnivoice-singing` finetune as an opt-in engine variant that *does* take `[happy]`/`[sad]`? (Recommendation: ship honest degradation now; the finetune is a separate engine-registry entry, a clean follow-up.)
- Q4. **g2p for IPA generation:** bundle a small grapheme→IPA helper (e.g. `g2p-en` for English, ARPABET) so users get a suggested phoneme string, or keep this spec validation-only (user supplies IPA/CMU)? (Recommendation: validation-only now; g2p is a per-language native-dep rabbit hole — defer, document.)
- Q5. **Docs-sync:** this adds inline-tag and pronunciation surfaces → `docs/voice-design.md`, `docs/generation-parameters.md`, and `docs/features.yaml` must update in the same PRs (hard rule). Confirm whether a dedicated `docs/expressive-tts.md` page is wanted.
