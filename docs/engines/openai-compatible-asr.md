# VoiceStudio: OpenAI-Compatible ASR

Point transcription at **any** server exposing an OpenAI-compatible
`POST /v1/audio/transcriptions` endpoint: gigastt, LM Studio, or a
llama.cpp-style server on the same machine; a self-hosted
Qwen3-ASR/FunASR/SenseVoice box on your network; Groq; or OpenAI's Whisper
API. VoiceStudio is a pure client in this mode, so the configured server owns
model installation and compute.

## Setup

Everything lives on one screen — **Model Catalogue**, **ASR** tab:

1. The **OpenAI-compatible (remote server)** row shows as unavailable until
   a server is configured. The config panel appears **below the engine
   list** while the ASR tab is selected.
2. Set **Server URL** to your server's base URL (see the examples below).
3. Set **Model** to whatever your server expects (`whisper-1` for OpenAI's
   API; check your server's docs or its `/v1/models` listing otherwise).
4. **API key** is optional — many self-hosted servers accept requests
   without one. Set it if your server requires auth. The key is stored
   encrypted on your machine and is never displayed or sent anywhere except
   the server you configured.
5. Click **Test connection**. This saves the fields, then sends one tiny
   `GET /models` request to the server — no audio is uploaded, nothing is
   transcribed. You'll see the round-trip latency on success (plus whether
   your configured model is in the server's list), or the exact failure
   (unreachable / timeout / rejected key / HTTP status) if not.
6. Click **Use** on the engine's row to make it the active ASR engine — the
   same picker every engine family has. Power users can pin it instead with
   `OMNIVOICE_ASR_BACKEND=openai-compat-asr` before launching; the env var
   always wins over the Settings pick.

Config changes apply on the next transcription — no restart needed. The
engine is never active by default: VoiceStudio's ASR auto-detect only ever
picks local engines, and the app works fully with this engine unconfigured.

## Examples

| Server | Server URL | Model | API key |
| --- | --- | --- | --- |
| [gigastt](https://github.com/ekhodzitsky/gigastt) (local Russian specialist) | `http://127.0.0.1:9876/v1` | `gigaam-v3-rnnt` | none |
| LM Studio (local) | `http://localhost:1234/v1` | the model name shown in LM Studio | none |
| llama.cpp / whisper.cpp server (local) | `http://localhost:8080/v1` | whatever the server loads (often ignored) | none |
| speaches / faster-whisper-server (local) | `http://localhost:8000/v1` | e.g. `Systran/faster-whisper-large-v3` | none |
| Self-hosted Qwen3-ASR / FunASR (LAN box) | `https://<host>:8000/v1` | your deployment's model id | if you enabled auth |
| Groq | `https://api.groq.com/openai/v1` | `whisper-large-v3` | required |
| OpenAI | `https://api.openai.com/v1` | `whisper-1` | required |

Plain HTTP is accepted only for exact loopback hosts such as `localhost`,
`127.0.0.1`, and `::1`. Every non-loopback endpoint must use HTTPS. VoiceStudio
does not follow redirects from transcription or connection-probe requests.

Local servers vary in which endpoints they implement — if **Test
connection** reports the server is reachable but doesn't list models,
transcription may still work; run a small dictation or dub-transcribe to
confirm.

## Response format

The backend prefers `response_format=verbose_json` for real per-segment
timestamps (OpenAI's API and most compatible servers support it) and falls
back to plain text automatically if your server rejects that format. Neither
path returns word-level timestamps — that's not part of this API.

When this engine serves VoiceStudio's own `/v1/audio/transcriptions`, the
caller's `language`, `prompt` and `temperature` are forwarded to your server,
and `/v1/audio/translations` calls your server's `/audio/translations`.

## Privacy note

Audio goes only to the server **you** configure. A loopback URL such as the
gigastt example keeps it on the same machine and may use HTTP. LAN and public
endpoints require HTTPS, and redirects are not followed. Review the configured
server's data handling before sending anything sensitive.
