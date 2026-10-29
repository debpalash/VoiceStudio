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
| Laughter or a sigh | ⊕ Insert → `[laughter]` / `[sigh]` | Default engine (VoiceStudio), CosyVoice 3 (`[laughter]`) |
| An audible breath **on demand** | ⊕ Insert → `[breath]` | CosyVoice 3 only (opt-in) — see [Breaths](#breaths-specifically) |
| Whispering | Style → `whisper` (the voice-design/style field) | Default engine |
| Emotion ("scared, then excited", graded intensity) | **Voice Cloning → Emotion**: sliders or a description on IndexTTS2, a written delivery direction on CosyVoice 3 and VoxCPM2. Also Audiobook Production Overrides (IndexTTS2) and the `/generate` / `/ws/tts` APIs | Opt-in engines only |
| The same take again | Pin the seed / lock the profile | Default engine |

The **Emotion** button sits beside Synthesize on every engine. On an engine
that can't take emotion it says so and names the engines that can, and the
⊕ Insert menus (Voice Cloning's script and Audiobook's **Reactions**) gray out
tags the active engine would read aloud as words. The first take you render
shows a one-time tip pointing at both.

## Recovering an interrupted audiobook

Leaving the Audiobook tab interrupts synthesis at a chapter boundary. When you
return, the recovery card can reuse completed chapters and render only the
remainder; **Open chapter cache** reveals the cached audio folder.

Cached chapters are matched by their script, voice and render settings, not by where the data folder lives, so they stay reusable after the folder is moved (in **Settings → Storage** or by hand) or the machine loses power. One exception: chapters cached by an earlier version render once more if the folder is moved by hand before this version has been launched from the old location. When a chapter you expected to reuse renders again, the backend log names the input that changed (for example a voice's reference text or the pronunciation lexicon); the diagnostic bundle includes that log.

## Why bracket tags work at all (and when they don't)

Everything you type in the text box reaches the active engine **verbatim** —
the pipeline goes out of its way not to break tags:

- Tilde-separated integer, signed, and decimal ranges get a spoken separator in
  English, Korean, Japanese, and Chinese; malformed chains and product codes
  are left unchanged.
- Zero-width joiners and non-joiners used in Persian, Urdu, Indic text and
  emoji survive normalization; unsafe invisible and direction controls are
  still removed.
- The text-normalization pass (numbers, abbreviations, markdown emphasis)
  skips every `[…]` span (`backend/services/text_normalization.py`).
- The long-text chunker never cuts inside a bracket tag
  (`backend/services/chunked_tts.py`, `_BRACKET_TAG_RE`).

The flip side is just as important: **unrecognized tags are not stripped.**
An engine that doesn't know a tag receives it as literal text and will try to
speak it. Pasting an ElevenLabs script full of `[excited]` / `[whispers]`
degrades output on every engine we ship — those tags are on the roadmap (see
[What's coming](#whats-coming)), not in the engines. Use only the tags listed
for your engine below.

**A tag that is already in the text is caught on render, not just in the menus.**
`backend/services/tag_policy.py` audits the final text against the engine's
declared `expression_caps.tags`; when it finds reaction tags the active engine
will read as words, the take's response carries `X-OmniVoice-Tag-Warning`
(count) and `X-OmniVoice-Tag-Warning-Tags` (percent-encoded tags) on both the
classic WAV and the streaming preview, and the app shows one warning toast
naming them. The text itself is never rewritten — the warning tells you which
part will sound wrong. App-level markers (`[pause …]`, the SSML-lite tags,
`[voice:…]`, `[[…]]` overrides) are handled before any engine sees them and are
never part of that warning.

## What each engine can do today

Clients read the same answer from `GET /engines/tts`: each engine carries
`expression_caps` — `emotion` is `graded` (IndexTTS2), `direction` (written
`instruct` steers a cloned voice: CosyVoice 3, VoxCPM2) or `none`, and `tags`
lists the reaction tags its tokenizer renders.

### Every engine

- **`[pause Nms]` markers** — `[pause]` (350 ms default), `[pause 500ms]`,
  `[pause 1s]`, up to 10 s. Rendered as real stitched silence, so it works
  identically on all engines. **Settings → Pronunciation dictionary → Pauses →
  Fill pauses with room tone** (off by default; `OMNIVOICE_PAUSE_FILL=room_tone`
  wins over the switch) fills these gaps, and audiobook line and paragraph gaps,
  with noise shaped like the take's own quiet background instead of digital
  zeros, faded over 40 ms into the lines on each side, so the background no
  longer cuts out at every pause. It is derived from the generated take, needs
  no model, is reproducible, and falls back to silence when the take has no
  usable quiet floor. Long-text chunk joins are not changed.
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

Pronunciation dictionary matching uses Unicode case-insensitive literal matches. Each matched term uses its own respelling; distinct terms such as Straße and STRASSE can have different respellings. Longer terms win overlaps, and later equal-length case variants retain precedence. Because that precedence follows entry order, reordering such variants re-renders cached longform chapters; reordering unrelated entries does not.

### Default engine (VoiceStudio)

**Non-verbal tags.** The bundled model natively tokenizes 13 reaction tags
(`omnivoice/models/omnivoice.py`, `_NONVERBAL_PATTERN`) — the ⊕ Insert button
at the corner of the Script box offers them all while this engine is active:

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

The Audiobook panel adds two additional production controls:
IndexTTS2 emotion (also available in Voice Cloning; see below), and **Vary repeated lines** — a cache opt-out
that gives every identical line its own take instead of replaying one recording
(off by default, so books stay byte-reproducible unless you ask for variety).

**Joins between lines.** Every engine pads each render with its own lead-in and
tail, from tens to hundreds of milliseconds, so the pauses you hear between lines
are whatever the engine happened to add. The Audiobook and Stories production
controls make that explicit: **Trim engine silence** cuts everything quieter than
-40 dBFS from both ends of each render (keeping 40 ms), **Gap between lines**
and **Gap between paragraphs** then insert exactly the silence you choose, up to
5 seconds each. With both gaps at zero and trimming off, joins are the old hard
joins and cache keys are unchanged, including renders that use a seed or
emotion override, so existing books are not re-rendered. An explicit `[pause]`
replaces the line gap after its line instead of adding to it. Markup that splits
one line (`[slow]`, `[fast]`, `[emphasis]`, `[spell]`) is still one line: there
is no gap inside it, except that a blank line at the split gets the paragraph gap.
A `[voice:NAME]` change is a line boundary and gets the line gap. A paragraph gap
above zero renders the paragraphs of a line as separate engine calls; at zero a
line is one call, exactly as before. The join stage adds at most 15 minutes of
silence to a chapter; a request beyond that fails before any synthesis or cache
read, with a message to lower the gaps or split the chapter. Values around 250 ms
for lines and 350 ms for paragraphs suit most narration, but they are explicit
choices: the defaults are 0.

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
`CosyVoiceBackend`). In **Voice Cloning**, open **Emotion** and write the
direction in **Delivery direction**; it reaches the model as written. The API
takes the same text as the `instruct` form field of `POST /generate` (or
`/ws/tts`).

Setup: clone + install [CosyVoice](https://github.com/FunAudioLLM/CosyVoice)
(non-trivial: `git clone --recursive`, its requirements, SoX), then set
`OMNIVOICE_COSYVOICE_MODEL` to the model directory and select it in
Model Catalogue. CUDA or CPU; MPS is unverified upstream.

### VoxCPM2 (opt-in)

VoxCPM2's native convention is an instruct prefix inside the text itself:
`(speaking fast, out of breath) I can't stop now.` The app maps the
`instruct` field onto that prefix (`backend/services/tts_backend.py`,
`VoxCPM2Backend.generate`), and because the convention is literally in-text,
typing the parenthetical at the start of your text works too. In **Voice
Cloning**, **Emotion → Delivery direction** fills that field for a cloned
voice. Treat it as guidance, not a guarantee — adherence varies by voice and
language.

### IndexTTS2 (opt-in)

The only engine with **graded** emotion control: an 8-value emotion vector
(happy, angry, sad, afraid, disgusted, melancholic, surprised, calm), an
emotion *reference clip* whose delivery is mimicked (with a blend strength),
or a natural-language emotion description.

In **Voice Cloning**, select IndexTTS2 and open the **Emotion** (heart-pulse)
button beside Voice controls. The default is **Off**. Choose **Description**
for a free-form emotion instruction with an intensity slider (0–0.6, the
recommended maximum for text emotion in IndexTTS2), or choose **Sliders** for
the eight individual emotion weights (0–1). The reference audio continues to
define the speaker's voice.

The **Audiobook** tab's Production Overrides also support the description and
intensity; with any other engine those two fields stay visible but disabled. The multipart **`POST /generate`** API accepts either `emo_text`
plus optional `emo_alpha`, or `emo_vector` as a JSON array of exactly eight
finite numbers between 0 and 1. Mixing the two methods, supplying intensity
without text, or choosing an unsupported engine returns HTTP 422 before
loading any model. Classic audio responses and NDJSON streaming previews
(`stream=true`) pass the same emotion controls to the engine.

An independent **emotion reference clip** remains available via the WebSocket
API `/ws/tts` (`emo_audio`, alongside the other controls;
`backend/api/routers/tts_stream.py`). It is not yet exposed in Voice
Cloning's Emotion popover.

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
pronunciation phases have shipped (dictionary + `[[…]]` overrides), and so
has the capability disclosure: Voice Cloning's **Emotion** button and the
Audiobook emotion fields appear on every engine and say what the active one
can do, and the Insert menus offer only the tags it renders. Still spec'd, not
shipped: the engine-agnostic inline emotion/reaction tag grammar (per-span
emotion inside one take), the emotion-reference clip picker, and a written
direction for Audiobook renders. No
promised date — when each lands, this page gets updated in the same PR.

Omitted API join options keep legacy hard joins (zero gaps, no edge trim).
Clients enable seamless joins by sending their chosen gaps and trim explicitly.
