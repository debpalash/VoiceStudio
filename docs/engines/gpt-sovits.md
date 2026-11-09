# VoiceStudio — GPT-SoVITS Engine

GPT-SoVITS (RVC-Boss) is one of the most popular open-source voice-cloning
systems (57k+ GitHub stars, MIT-licensed). It does zero-shot and few-shot
cloning with excellent naturalness in Chinese, English, Japanese, Cantonese,
and Korean, and it is very fast (RTF ~0.014 on suitable hardware).

Unlike VoiceStudio's other engines, GPT-SoVITS does not run inside the app.
It ships as a standalone API server, and VoiceStudio connects to it over
HTTP.

## When to pick it

- You already run (or want to run) a GPT-SoVITS server, e.g. with few-shot
  fine-tuned voices.
- You need fast, natural cloning in zh/en/ja/yue/ko.

## Setup

1. Install and start the GPT-SoVITS API server (upstream project):

   ```bash
   cd GPT-SoVITS
   python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
   ```

   VoiceStudio talks to the `api_v2` protocol (`POST /tts`). The older v1
   `api.py` server is not compatible. To use fine-tuned voices, edit the
   `custom` section of `tts_infer.yaml` so it points at your own
   `GPT_weights*` and `SoVITS_weights*` files before starting the server.

2. Select the engine via **Model Catalogue** (TTS tab → **Use**) or
   `OMNIVOICE_TTS_BACKEND=gpt-sovits`.

VoiceStudio marks the engine available only when the server responds
(2-second reachability probe).

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `OMNIVOICE_GPTSOVITS_URL` | `http://127.0.0.1:9880` | API server URL |
| `OMNIVOICE_GPTSOVITS_REF_LANG` | `auto` | Language of the clip (`zh`/`en`/`ja`/`yue`/`ko`) |
| `OMNIVOICE_TRUSTED_NETWORKS` | (unset) | Required to allow a non-loopback server |
| `OMNIVOICE_GPTSOVITS_REF_AUDIO` | (unset) | Default reference clip, 3-10 s, as a path the server can read; used when a request has no voice profile |
| `OMNIVOICE_GPTSOVITS_REF_TEXT` | (empty) | Exact transcript of that default clip |

**Remote servers:** by default VoiceStudio only talks to loopback addresses
— part of the local-first guarantee. To point at a server on another
machine (e.g. a GPU box on your LAN), add its network to
`OMNIVOICE_TRUSTED_NETWORKS`; otherwise the connection is refused as an
untrusted endpoint.

Prefer `https://` (or a private tunnel such as Tailscale/WireGuard) for any
non-loopback server: with plain `http://` the text you synthesize and the
audio that comes back cross the network unencrypted. VoiceStudio does not
disable certificate verification, so a TLS endpoint needs a certificate the
system trusts.

## Behaviour notes

- Output is 32 kHz mono (server output is resampled if needed).
- Requests go out unsplit (`cut0`); VoiceStudio chunks long text itself.
  Letting the server split on punctuation produces short fragments that can
  end early.
- Every request needs a reference clip. A voice profile supplies one; plain
  TTS uses the two default-clip variables above and fails with an error
  naming them when they are missing.
- The reference path is opened by the GPT-SoVITS server process, not by
  VoiceStudio, so a remote server needs the clip on its own disk. A clip that
  ends in silence, or a transcript that does not match it, gives
  near-silent output.
- Speed control is forwarded as the server's `speed_factor`.
- The GPU is whatever the GPT-SoVITS server itself uses (CUDA preferred);
  VoiceStudio's side is just an HTTP client.

## Known limits

- Five languages only; for broader coverage use
  [OmniVoice](omnivoice.md) ([languages.md](../languages.md)).
- No voice design; server availability is your responsibility — if the
  server stops, generations fail with a "server not reachable" error.

## Troubleshooting

- "GPT-SoVITS server not reachable": start the server or correct
  `OMNIVOICE_GPTSOVITS_URL`. The availability check is a validator-only
  `GET /tts?text=&text_lang=en&prompt_lang=en`. Answers of 400, 200 or 405
  count as reachable. A 404 means there is no `/tts` route, which points to a
  v1 server or a proxy mismatch. Any other status is reported with its number
  and marks the engine unavailable. Redirects are refused rather than
  followed and show up as unreachable, so use the server's direct origin.
- "endpoint is outside loopback or OMNIVOICE_TRUSTED_NETWORKS": see
  Configuration above.
- Other issues: [install/troubleshooting.md](../install/troubleshooting.md).

See also: [benchmarks.md](../benchmarks.md),
[expressive-speech.md](../expressive-speech.md).

Reference transcripts from saved profiles and uploaded clips use api_v2's
`auto` language detection, independently of the output language. An English
reference can therefore produce Japanese speech without being interpreted as
Japanese. The configured default clip also uses `auto` unless
`OMNIVOICE_GPTSOVITS_REF_LANG` specifies its language; that setting never
applies to a different, explicitly supplied clip.
