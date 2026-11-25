# ElevenLabs-compatible API

Apps, SDKs and automations written for ElevenLabs can use VoiceStudio instead by
changing one setting: the base URL. Point them at
`http://<host>:<port>/elevenlabs` (for the desktop app on the same machine,
`http://127.0.0.1:3900/elevenlabs`). Everything runs on your machine; nothing is
sent to ElevenLabs.

```python
import os

from elevenlabs.client import ElevenLabs

client = ElevenLabs(
    base_url="http://127.0.0.1:3900/elevenlabs",
    # The backend's API key when it has one; leave it unset otherwise. A wrong
    # key is refused even where no key is needed (Docker, a trusted network).
    api_key=os.environ.get("OMNIVOICE_API_KEY"),
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
| `POST /v1/speech-to-text` | Uses the active speech-recognition engine; returns `words` with `spacing` items. `diarize=true` adds `speaker_id` to each word; if speaker identification can't run, the transcript comes back without labels and `X-OmniVoice-Diarization-Error` says why. |
| `POST /v1/audio-isolation` | The voice from a recording, with music and background removed (Demucs, as in Dubbing). The request never downloads a model: until vocals have been separated once in the app (Dubbing or Clean audio), it returns `409 audio_isolation_model_missing`. |
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
- **Output formats:** `mp3_<rate>_<kbps>` (for example `mp3_44100_128` or
  `mp3_22050_32`), `opus_48000_<kbps>`, `wav_<rate>`, `pcm_8000`, `pcm_16000`,
  `pcm_22050`, `pcm_24000`, `pcm_44100`, `pcm_48000`, `ulaw_8000` and
  `alaw_8000`, each delivered at exactly the rate and bitrate it names; other
  combinations return 422. MP3 takes the standard bitrates for its rate: 32–320
  kbps (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320) at 32 kHz
  and above, 32–160 kbps (32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160) below.
  PCM and WAV are 16-bit; PCM is little-endian mono.
- **Voice settings:** `speed` is applied. `stability`, `similarity_boost`,
  `style` and `use_speaker_boost` have no VoiceStudio equivalent and are
  accepted without effect.
- **Also applied:** `language_code`, `seed`, and your
  [pronunciation dictionary](expressive-speech.md).
- **Limits:** up to 4,096 characters per text-to-speech request (use the
  Long-form page for longer scripts) 2 GB per recording for speech to text and voice isolation, and 64 MB for
  the voice changer.
- **Errors** use ElevenLabs' shape, `{"detail": {"status": "...", "message": "..."}}`.

Generated audio carries VoiceStudio's AI watermark, as on every other route.

## Authentication

Requests from the same machine need no key. Remote requests need the backend's
API key, sent as `xi-api-key` (what ElevenLabs SDKs send) or as
`Authorization: Bearer <key>`. `xi-api-key` is accepted only on `/elevenlabs`
routes. See [API authentication](api-auth.md).
