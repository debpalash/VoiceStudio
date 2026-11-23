# ElevenLabs-compatible API

Apps, SDKs and automations written for ElevenLabs can use VoiceStudio instead by
changing one setting: the base URL. Point them at
`http://<host>:<port>/elevenlabs` (for the desktop app on the same machine,
`http://127.0.0.1:3900/elevenlabs`). Everything runs on your machine; nothing is
sent to ElevenLabs.

```python
from elevenlabs.client import ElevenLabs

client = ElevenLabs(
    base_url="http://127.0.0.1:3900/elevenlabs",
    api_key="local",  # any value on this machine; the backend's API key when remote
)
voices = client.voices.search().voices
audio = client.text_to_speech.convert(
    voice_id=voices[0].voice_id,
    text="Hello from VoiceStudio.",
    model_id="eleven_multilingual_v2",
    output_format="mp3_44100_128",
)
with open("hello.mp3", "wb") as f:
    for chunk in audio:
        f.write(chunk)
```

```bash
curl http://127.0.0.1:3900/elevenlabs/v1/text-to-speech/default?output_format=pcm_16000 \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello from VoiceStudio."}' --output hello.pcm
```

## Endpoints

| Endpoint | Notes |
|---|---|
| `POST /v1/text-to-speech/{voice_id}` | Whole file. |
| `POST /v1/text-to-speech/{voice_id}/stream` | PCM, μ-law and A-law stream sentence by sentence, or chunk by chunk on engines with native streaming. |
| `POST /v1/speech-to-speech/{voice_id}` | Voice changer: says the uploaded speech in a voice profile's voice, keeping its timing and intonation (the app's Convert → Keep my delivery). Without a voice-conversion engine it transcribes and re-speaks the clip instead and sets `X-OmniVoice-Fallback: respoken`. |
| `POST /v1/sound-generation` | Sound effects from a description; needs Tools → Sound effects installed. `prompt_influence` and `loop` are accepted without effect. |
| `POST /v1/speech-to-text` | Uses the active speech-recognition engine; returns `words` with `spacing` items. |
| `GET /v1/voices`, `GET /v2/voices`, `GET /v1/voices/{voice_id}` | Your voice profiles (`cloned`) plus `default`, the active engine's own voice. |
| `GET /v1/voices/settings/default`, `GET /v1/voices/{voice_id}/settings` | ElevenLabs' default values, for clients that read them first. |
| `GET /v1/models` | The ElevenLabs model ids below, plus every installed VoiceStudio engine. |

## How requests map

- **Voices:** `voice_id` is a voice-profile id from `GET /v1/voices`, `default`,
  or an engine preset name. An ElevenLabs voice id (20 letters and digits) that
  is not one of your profiles returns `404 voice_not_found`.
- **Models:** `eleven_*` model ids (`eleven_multilingual_v2`,
  `eleven_flash_v2_5`, `eleven_v3`, …) use the engine that is active in the app.
  A VoiceStudio engine id, such as `voxcpm2`, uses that engine.
- **Output formats:** `mp3_*`, `opus_48000_*`, `wav_*`, `pcm_8000` to
  `pcm_48000`, `ulaw_8000` and `alaw_8000`. PCM is 16-bit little-endian mono at
  the requested rate.
- **Voice settings:** `speed` is applied. `stability`, `similarity_boost`,
  `style` and `use_speaker_boost` have no VoiceStudio equivalent and are
  accepted without effect.
- **Also applied:** `language_code`, `seed`, and your
  [pronunciation dictionary](expressive-speech.md).
- **Limits:** up to 4,096 characters per request; use the Long-form page for
  longer scripts. Speech to text does not label speakers yet (`diarize=true`
  returns 422); the Transcriptions page does.
- **Errors** use ElevenLabs' shape, `{"detail": {"status": "...", "message": "..."}}`.

Generated audio carries VoiceStudio's AI watermark, as on every other route.

## Authentication

Requests from the same machine need no key. Remote requests need the backend's
API key, sent as `xi-api-key` (what ElevenLabs SDKs send) or as
`Authorization: Bearer <key>`. `xi-api-key` is accepted only on `/elevenlabs`
routes. See [API authentication](api-auth.md).
