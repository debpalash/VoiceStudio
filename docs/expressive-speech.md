# Expressive speech: breaths, laughter, and style

How to direct a performance — laughter, sighs, pauses, whispering, emotion,
and the community favorite: *"how do I make it take a sharp audible breath,
like a person running out of breath?"* Some of this is supported today
(engine-dependent), some is spec'd but not shipped yet. This page tells you
exactly which is which, so you don't burn an evening on tags an engine
ignores.

## The short version

| You want | Do this | Works on |
|---|---|---|
| A pause | Type `[pause]`, `[pause 500ms]`, or `[pause 1.5s]` in the text | Every engine |
| Laughter or a sigh | ⊕ Insert → `[laughter]` / `[sigh]` | Default engine (VoiceStudio) |
| An audible breath **on demand** | `[breath]` in the text | CosyVoice 3 only (opt-in) — see [Breaths](#breaths-specifically) |
| Whispering | Style → `whisper` (the voice-design/style field) | Default engine |
| Emotion ("excited", "sad", graded intensity) | IndexTTS2's emotion controls — Audiobook tab's Production Overrides, or the `/ws/tts` API — or CosyVoice 3 instruct | Opt-in engines only |
| The same take again | Pin the seed / lock the profile | Default engine |

## Recovering an interrupted audiobook

Switching away from the Audiobook tab explicitly interrupts synthesis at a chapter boundary. The Audiobook recovery card lets you resume with its cached chapters, and **Open chapter cache** reveals the chapter audio cache.

Cached chapters are matched by their script, voice and render settings, not by where the data folder lives, so they stay reusable after the folder is moved (in **Settings → Storage** or by hand) or the machine loses power. One exception: chapters cached by an earlier version render once more if the folder is moved by hand before this version has been launched from the old location. When a chapter you expected to reuse renders again, the backend log names the input that changed (for example a voice's reference text or the pronunciation lexicon); the diagnostic bundle includes that log.

## Why bracket tags work at all (and when they don't)

Everything you type in the text box reaches the active engine **verbatim** —
the pipeline goes out of its way not to break tags:

- Tilde-separated integer, signed, and decimal ranges get a spoken separator in
  English, Korean, Japanese, and Chinese; malformed chains and product codes
  are left unchanged.
- The text-normalization pass (numbers, abbreviations) skips every `[…]` span
  (`backend/services/text_normalization.py`).
- The long-text chunker never cuts inside a bracket tag
  (`backend/services/chunked_tts.py`, `_BRACKET_TAG_RE`).

The flip side is just as important: **unrecognized tags are not stripped.**
An engine that doesn't know a tag receives it as literal text and will try to
speak it. Pasting an ElevenLabs script full of `[excited]` / `[whispers]`
degrades output on every engine we ship — those tags are on the roadmap (see
[What's coming](#whats-coming)), not in the engines. Use only the tags listed
for your engine below.

## What each engine can do today

### Every engine

- **`[pause Nms]` markers** — `[pause]` (350 ms default), `[pause 500ms]`,
  `[pause 1s]`, up to 10 s. Rendered as real stitched silence, so it works
  identically on all engines.
- **Punctuation** — ellipses, dashes, exclamation marks, and short fragments
  genuinely shape pacing and intonation. Cheap, underrated.
- **The reference clip is a performance direction.** Zero-shot cloning mirrors
  the *delivery* of the reference, not just the timbre — a flat reference
  clones flat, an animated one clones animated (see the tip in
  [generation-parameters.md](generation-parameters.md)). This is the most
  reliable expressive control in the app.
- **Pronunciation overrides** — `[[Nuh-VAD-uh]]` inline, or the pronunciation
  dictionary. Not expression, but often what a "it says this weirdly" problem
  actually needs.

### Default engine (VoiceStudio)

**Non-verbal tags.** The bundled model natively tokenizes 13 reaction tags
(`omnivoice/models/omnivoice.py`, `_NONVERBAL_PATTERN`) — the ⊕ Insert button
at the corner of the Script box lists them all:

`[laughter]` `[sigh]` `[confirmation-en]` `[question-en]` `[question-ah]`
`[question-oh]` `[question-ei]` `[question-yi]` `[surprise-ah]`
`[surprise-oh]` `[surprise-wa]` `[surprise-yo]` `[dissatisfaction-hnn]`

Honest expectations: `[laughter]` and `[sigh]` are the broadly useful ones;
most of the interjection variants (`-ah`, `-yi`, `-hnn`) are tuned for
Mandarin-flavored speech. How convincingly a tag renders varies with the
voice — a tag that lands great on one reference clip can come out subdued on
another. There is **no intensity control**, and **no `[breath]` tag** in this
set.

**Whispering.** `whisper` is the one delivery style the instruct validator
accepts (the taxonomy is Gender / Age / Pitch / Style / Accent / Dialect —
see [voice-design.md](voice-design.md)). `[happy]` / `[sad]`-style emotion
direction is **not** something the base model takes.

**Sampling knobs + seed.** The Voice workspace **and the Audiobook tab** each
carry a Production Overrides panel exposing the sampling surface (defaults in
parentheses; details in [generation-parameters.md](generation-parameters.md)):

- `position_temperature` (5.0) and `class_temperature` (0.0) — 0 is greedy;
  higher is more random, which means more expressive variation *and* more
  artifacts.
- `num_step` — the Voice page defaults to 16 (fast); Audiobook renders default
  to 32 (cleaner), overridable in the Audiobook panel. Fewer steps = rougher,
  occasionally more "human-sounding" edges.
- **Seed** — unpinned by default, so every render differs. The history rail
  shows the seed each take used; "Keep this seed" (Design tab) or locking a
  profile from history pins reference + seed, making the voice
  bit-reproducible. The Audiobook panel also takes a book-level seed directly.
- `postprocess_output` (on) — removes long silences from the output. Turn it
  off when the silence *is* the performance.

The Audiobook panel adds two longform-only controls on top of that surface:
IndexTTS2 emotion (see below), and **Vary repeated lines** — a cache opt-out
that gives every identical line its own take instead of replaying one recording
(off by default, so books stay byte-reproducible unless you ask for variety).

It also owns the **joins**. Every engine pads each rendered line with its own
lead-in and tail silence (GPT-SoVITS ≈ 70 ms / 300 ms, others similar); joined
raw, a book reads as a string of separate takes. By default the renderer trims
that padding (**Trim engine silence**, −40 dBFS with 40 ms kept at each edge)
and inserts deliberate silence instead: **Gap between lines** (250 ms) between
consecutive lines that carry no `[pause]` of their own — an explicit `[pause]`
replaces the gap rather than adding to it — and **Gap between paragraphs**
(350 ms) at a blank line inside one line of script. A line that inline markup
(`[slow]`, `[emphasis]`, `[spell]`) splits into several renders is still one
line: no gap lands in the middle of it (and a blank line sitting on that split
still gets the paragraph gap). A `[voice:NAME]` change is a line boundary and
gets the line gap, blank line or not — the paragraph gap is for breaks inside
one voice's text. With the paragraph gap at 0 a line is
rendered in one engine call exactly as before, so setting both gaps to 0 and
turning trimming off gives the pre-existing hard joins (and their cache keys)
back, including cached renders with seed or emotion overrides. A chapter may
request at most 15 minutes of added join silence; larger requests fail before
synthesis or cache reads. Reduce the gaps or split the chapter to proceed.
Accepted gaps retain their exact duration on fresh renders and cache reuse.

**Longform-only tags.** Audiobook and Stories additionally parse SSML-lite —
`[slow]…[/slow]`, `[fast]…[/fast]`, `[emphasis]…[/emphasis]`, `[spell]` —
plus `[voice:NAME]` for multi-voice scripts
(`backend/services/longform_parser.py`). These are not parsed on the Voice
page.

### CosyVoice 3 (opt-in)

The most direct paralinguistic control in the app, if you're willing to
install it. CosyVoice 3 honors, inline in the text:

- `[breath]` — an audible breath, exactly where you put it
- `[laughter]`
- `<strong>word</strong>` — emphasis

plus **natural-language instruct** ("speak with a Sichuan accent", "sound
exhausted") — the backend appends the model's required `<|endofprompt|>`
terminator for you (`backend/services/tts_backend.py`,
`CosyVoiceBackend`). One catch: the Studio style field whitelists instruct to
the default engine's taxonomy, so free-text instruct currently needs the API
(`POST /generate` with an `instruct` form field, or `/ws/tts`).

Setup: clone + install [CosyVoice](https://github.com/FunAudioLLM/CosyVoice)
(non-trivial: `git clone --recursive`, its requirements, SoX), then set
`OMNIVOICE_COSYVOICE_MODEL` to the model directory and select it in
Model Catalogue. CUDA or CPU; MPS is unverified upstream.

### VoxCPM2 (opt-in)

VoxCPM2's native convention is an instruct prefix inside the text itself:
`(speaking fast, out of breath) I can't stop now.` The app maps the
`instruct` field onto that prefix (`backend/services/tts_backend.py`,
`VoxCPM2Backend.generate`), and because the convention is literally in-text,
typing the parenthetical at the start of your text works too. Treat it as
guidance, not a guarantee — adherence varies by voice and language.

### IndexTTS2 (opt-in)

The only engine with **graded** emotion control: an 8-value emotion vector
(happy, angry, sad, afraid, disgusted, melancholic, surprised, calm), an
emotion *reference clip* whose delivery is mimicked (with a blend strength),
or a natural-language emotion description. The **Audiobook tab's Production
Overrides** expose the natural-language path — an *Emotion description* field
plus an *Emotion strength* slider (`emo_alpha`), shown only when IndexTTS2 is
the active engine so there are no dead controls. The full surface (the 8-float
`emo_vector`, an `emo_audio` reference clip) is on the streaming WebSocket API
(`/ws/tts` — `emo_vector`, `emo_audio`, `emo_alpha`, `emo_text` fields;
`backend/api/routers/tts_stream.py`). The single-shot Voice page does not carry
emotion controls yet.

## Breaths, specifically

The honest answer to *"how do I invoke a sharp inhale on demand?"*:

**On the default engine — you can't yet, not directly.** There is no
`[breath]` or `[inhale]` token in its tag set; `[sigh]` is the nearest
neighbor and it's an exhale. An engine-agnostic breath/reaction tag layer is
spec'd ([specs/01-expressive-tts.md](specs/01-expressive-tts.md)) but not
shipped — see below.

**The direct route: CosyVoice 3.** Its `[breath]` tag puts an audible breath
exactly where you type it. If on-demand breaths matter to your work, this is
the supported path today.

**The coax-it recipe (default engine).** Breaths *can* be elicited — this is
exactly what v0.3.9 was doing by accident. Roughly in order of effectiveness:

1. **Put the breathing in the reference clip.** Record 8–15 s of yourself (or
   your speaker) genuinely winded — audible inhales between phrases. The
   clone mirrors the delivery. This alone gets most of the way there.
2. **Write for it.** Short gasping fragments with pauses:
   `I can't… [pause 300ms] I can't keep… [pause 200ms] keep running.`
3. **Turn off `postprocess_output`** (Production Overrides — Voice tab *or*
   Audiobook tab) so the silences — where breath artifacts live — aren't
   trimmed away.
4. **Add randomness, then farm takes.** Raise `class_temperature` to 0.3–0.7
   (default is 0, fully greedy) and regenerate a few times — the seed is
   unpinned, so each take differs. Both controls live in the same Production
   Overrides panel, so this whole recipe now works for an audiobook chapter
   (audition it with the per-chapter Preview button), not just a single Voice
   render. In a book, the **Vary repeated lines** toggle farms takes across
   repeated lines automatically.
5. **Keep the winner.** When a take breathes right, its seed is on the
   history entry — lock the profile from there (or set the Audiobook panel's
   book-level seed) and every future generation uses the same reference + seed.

Tradeoffs, stated plainly: temperature cuts both ways (the same randomness
that produces a great gasp produces slurred words and timbre drift), takes
are non-repeatable until you pin the seed, and postprocess-off keeps *all*
long silences, wanted or not. This is a workaround, not a feature — which is
why the feature is spec'd.

## Why v0.3.9-style random breaths faded

Users of v0.3.9 remember outputs that would spontaneously breathe, gasp, and
rustle — and noticed v0.3.15+ is smooth. Those breaths were never a feature:
they were uncontrolled sampling variance (unpinned seed + the default
`position_temperature` of 5.0) surviving an output chain that was, at the
time, less tidy. Then the chain got deliberately cleaner:

- **v0.3.12** — the mastering pre-stage was cut down to highpass + compressor
  after a field report of hidden echo; every generation had been getting a
  small room reverb baked in, which made outputs sound roomier and
  breathier (#986).
- **Silence post-processing** (`postprocess_output`, default on) removes long
  silences — the gaps where stray breath noise lived.
- **v0.3.16** — VoxCPM2 reference clips get edge-silence trimming before
  conditioning, and outputs get a trailing-silence trim (#1055), so that
  engine stopped inheriting dead air and its artifacts.

Net effect: the default output is now clean by design, and expressiveness is
becoming something you *ask for* (tags, instruct, the recipe above) rather
than something that happens to you.

## What's coming

[Spec 01 — Expressive TTS](specs/01-expressive-tts.md) defines the plan: one
engine-agnostic tag surface (`[excited]`, `[whispers]`, reaction tags like
`[breath]`) that lowers to whatever the active engine can really do and
**visibly degrades** where it can't, plus an Expression panel (emotion
dropdown + intensity + emotion-reference clip) everywhere in the UI. The
pronunciation phases have shipped (dictionary + `[[…]]` overrides), and the
Audiobook tab now carries a first Expression surface — IndexTTS2 emotion
description + strength, engine-gated. Still spec'd, not shipped: the
engine-agnostic inline emotion/reaction tag grammar, the emotion-reference
clip picker, and the Expression panel on the single-shot Voice page. No
promised date — when each lands, this page gets updated in the same PR.

Omitted API join options keep legacy hard joins (zero gaps, no edge trim).
Clients enable seamless joins by sending their chosen gaps and trim explicitly.
