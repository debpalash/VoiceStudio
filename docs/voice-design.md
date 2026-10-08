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

The attribute list above is OmniVoice's vocabulary. Engines that read a
description as free text — Qwen3-TTS VoiceDesign (MLX-Audio), VoxCPM2 and
audio.cpp — receive the Voice Design description exactly as you typed it, so
traits outside the list (such as "Scottish accent" or "raspy") reach the model.
Any details you pick in the app are appended as extra cues; details the app
mapped from your description are not re-sent. The engine catalogue reports
this as `instruct_vocabulary`: `"tags"` for the OmniVoice family,
`"freeform"` for everything else.

IndexTTS2, MOSS-TTS v1.5, MOSS-TTS-Nano, dots.tts, Confucius4, GPT-SoVITS,
Supertonic-3 and OmniVoice GGUF take their timbre from a reference clip or a
preset voice, so they can't design one. With one of them selected, the Design
workspace says so and links to the engine settings, and `/generate` answers a
design request (an `instruct`, a Voice Design recipe or a design voice with no
saved sample) with a 422. Cloning a saved design voice's sample still works on
them. The engine catalogue reports this as `supports_voice_design`: `false`
for these engines, `true` for engines that design, and `null` when an engine
doesn't declare it.

On every engine, a detail you pick holds until your description says something
different about it, and **Reset to description** drops all picks. Choosing a
saved voice or a starting point replaces the description.

Choosing a saved designed voice re-renders it from its saved sample, so it
sounds the same every time. Changing a detail, the description or the seed
turns the draft back into an unsaved design: takes follow your new settings,
and you can save them as a new voice. Through the API, a `/generate` request
whose `instruct` or `seed` differs from the design profile's designs from the
request instead of cloning the saved sample, and the take is not filed under
that profile.

Reopening a take restores the description, picks and details it was made with;
they are kept with the take in your local history. Takes made before this was
recorded come back with their full instruction as the description, so they
still render the same.

Gallery previews reject silent output and near-pure tonal buzz. The quality
check measures short audio frames rather than the whole clip, so longer or
softly voiced speech is not rejected merely for having low spectral flatness.
