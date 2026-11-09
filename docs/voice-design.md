# Voice Design

Voice Design mode lets you describe the desired speaker through speaker attributes (`instruct` parameter) — no reference audio needed. The model
generates a matching voice on the fly.

Saving a design does not load or download a voice engine. If the engine is
already loaded, Save also renders its identity sample. Otherwise the design
is saved with its attributes and the sample is generated when you preview it.
If the engine unloads before rendering starts, Save keeps the sample pending
instead of loading the engine again.
Until that preview exists, synthesis uses the saved attributes directly.

## Quick Example

```python
import torch
from omnivoice import OmniVoice

model = OmniVoice.from_pretrained(
    "k2-fsa/OmniVoice",
    device_map="cuda:0",
    dtype=torch.float16
)

audio = model.generate(
    text="This is a test for voice design.",
    instruct="female, young adult, high pitch, british accent",
)
```

## How It Works

The `instruct` parameter accepts a comma-separated string of speaker attributes.
Each attribute belongs to a **category** (gender, age, pitch, style, accent,
or dialect). Within a category, only one attribute may be selected at a time.
Attributes from different categories can be freely combined.

The model auto-detects the language of the instruct text and normalises it
internally — you can write in English, Chinese, or a mix of both.

## Supported Attributes

### Gender

| English | Chinese |
|---------|---------|
| male | 男 |
| female | 女 |

### Age

| English | Chinese |
|---------|---------|
| child | 儿童 |
| teenager | 少年 |
| young adult | 青年 |
| middle-aged | 中年 |
| elderly | 老年 |

### Pitch

| English | Chinese |
|---------|---------|
| very low pitch | 极低音调 |
| low pitch | 低音调 |
| moderate pitch | 中音调 |
| high pitch | 高音调 |
| very high pitch | 极高音调 |

### Style

| English | Chinese |
|---------|---------|
| whisper | 耳语 |

> `whisper` is the only delivery style the base model accepts — emotion tags like `[happy]`/`[sad]` are not part of this taxonomy. For everything expressive (breaths, laughter, pauses, emotion, and which engines support what), see [expressive-speech.md](expressive-speech.md).

### English Accent

Only effective when the synthesis text is in English.

| Accent |
|--------|
| american accent |
| british accent |
| australian accent |
| canadian accent |
| indian accent |
| chinese accent |
| korean accent |
| japanese accent |
| portuguese accent |
| russian accent |

### Chinese Dialect

Only effective when the synthesis text is in Chinese.

| Dialect |
|---------|
| 河南话 |
| 陕西话 |
| 四川话 |
| 贵州话 |
| 云南话 |
| 桂林话 |
| 济南话 |
| 石家庄话 |
| 甘肃话 |
| 宁夏话 |
| 青岛话 |
| 东北话 |

## Writing Instruct Strings

Separate attributes with commas (half-width `,` for English, full-width `，`
for Chinese — the model auto-fixes mismatches).

```
# English
"female, young adult, high pitch, british accent"

# Chinese
"女，青年，高音调，四川话"

# Mixed (auto-normalised)
"female, young adult, 四川话"
```

### Tips

- **Combine freely** across categories: `"male, elderly, low pitch, whisper"`.
- **Leave it to the model**: omit attributes you don't care about — the model
  fills in the rest. For example `"female"` alone is valid.
- **Case-insensitive**: `"Male"`, `"MALE"`, and `"male"` are all accepted, the code will normalize them to lower case.

- **Accent vs Dialect**: English accents are only applied to English speech, Chinese dialects are only applied to Chinese speech.

### Other engines

OmniVoice reads a fixed list of attributes (the tables above). Some engines
instead read free text: Qwen3-TTS VoiceDesign through MLX-Audio, VoxCPM2 and
audio.cpp. They get the description exactly as you typed it, so a trait that
is not on the OmniVoice list (a mood, a setting, a character) still reaches
the model. Details you pick by hand in the app are added after the
description as extra cues. Details the app only inferred from your wording are
not sent a second time, since the description already says them.

The engine catalogue tells clients which reading applies through
`instruct_vocabulary`: `"tags"` for the OmniVoice family and `"freeform"` for
every other engine.


IndexTTS2, MOSS-TTS v1.5, MOSS-TTS-Nano, dots.tts, Confucius4, GPT-SoVITS,
Supertonic-3, OmniVoice GGUF, PocketTTS, KittenTTS and Sherpa-ONNX take their
timbre from a reference clip or a preset voice and never read a description,
so they can't design one. With one of them selected, the Design
workspace says so and links to the engine settings, and `/generate` answers a
design request (an `instruct`, a Voice Design recipe or a design voice with no
saved sample) with a 422. On those that clone, a saved design voice's sample
still works; KittenTTS and Sherpa-ONNX use only their preset voices. On a preset-voice engine, the Home composer leaves out any style
instruction, which those engines can't read, so the preset take still runs. The engine catalogue reports this as `supports_voice_design`: `false`
for these engines, `true` for engines that design, and `null` when an engine
doesn't declare it.

On every engine, a detail you pick stays in force until the description says
something different about that same detail. **Reset to description** drops all
of your picks so the written description decides again; it appears only once
a pick or a saved voice is overriding a written description. Choosing a saved
voice or a starting point replaces the description. Accent and Chinese dialect
details keep their model tokens in the request (the Sichuan dialect token, for
example), while the app shows them in your interface language.


An open take lists the language it was rendered in. When the composer is set
to a different language, the take says which one new takes will use, and the
take pane moves to each new take as it renders. In narrow windows the take
pane opens as a drawer over the script; **Recent takes** next to the voice name
reopens it.

Choosing a saved designed voice re-renders it from its saved sample, so it
sounds the same every time. Changing a detail, the description or the seed
turns the draft back into an unsaved design: takes follow your new settings,
and you can save them as a new voice. Through the API, a `/generate` request
whose `instruct` or `seed` differs from the design profile's designs from the
request instead of cloning the saved sample, and the take is not filed under
that profile.

The seed reaches every engine that designs, so a new seed gives a new take.
Engines with their own random generator (VoxCPM2, CosyVoice, audio.cpp,
MLX-Audio and the OmniVoice sidecar, plus OmniVoice GGUF for cloning) receive
it with each request.

Reopening a take brings back the description, the picks and the details it
was made with. These are kept with the take in your local history. A take
made before this was stored comes back with its full instruction as the
description and looks the same as it did.


Gallery previews reject silent output and near-pure tonal buzz. The quality
check measures short audio frames rather than the whole clip, so longer or
softly voiced speech is not rejected merely for having low spectral flatness.
