# Twilio: answer phone calls with a saved voice

**Integrations → Twilio** answers calls to your Twilio phone number in one of
your saved voices. When someone calls, VoiceStudio speaks your greeting over a
[Twilio Media Stream](https://www.twilio.com/docs/voice/media-streams) and then
hangs up. Synthesis runs on your computer with your chosen voice and engine.
Twilio receives the 8 kHz phone audio it plays to the caller.

The integration is **off by default**. Nothing listens and nothing leaves your
computer until you turn it on. It then needs a public HTTPS tunnel that you run.

## What you need

- A Twilio account and a phone number that supports voice calls. Calls are
  billed by Twilio.
- A tunnel that gives your computer a public `https://` address, such as
  [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
  or [ngrok](https://ngrok.com/docs/). Twilio cannot reach `127.0.0.1`.
- A working TTS engine in VoiceStudio. A saved voice profile is optional; without
  one, the engine's default voice is used.

## Setup

1. Open **Integrations → Twilio**. Enter your **Account SID** and **Auth Token**
   from the Twilio Console home page. Choose a voice and engine, and write the
   greeting (up to 1,000 characters).
2. Choose **Test locally** to hear the greeting at phone quality. It is resampled
   to 8 kHz, μ-law encoded and decoded again, exactly as a caller hears it. This
   runs entirely on your computer and also pre-renders the greeting, so the
   first real call starts speaking immediately.
3. Start your tunnel pointed at the telephony listener's port (**3950** unless
   you set `OMNIVOICE_TWILIO_PORT`), **not** at VoiceStudio's main port:

   ```sh
   cloudflared tunnel --url http://127.0.0.1:3950
   # or
   ngrok http 3950
   ```

4. Paste the tunnel's `https://…` address into **Public tunnel URL** and save.
   Use only the origin, without a path. The page then shows the **Voice webhook URL**
   (`https://<your-tunnel>/integrations/twilio/voice`).
5. Turn on **Answer calls**. VoiceStudio starts the separate telephony listener
   on `127.0.0.1` and shows its address as the **tunnel target**. If port 3950
   was taken, the listener uses the next free port; restart the tunnel against
   the tunnel target shown.
6. In the Twilio Console, open **Phone Numbers → Manage → Active numbers**, select
   your number, and under **Voice configuration** set **A call comes in** to
   **Webhook**, the Voice webhook URL, and **HTTP POST**. Save.
7. Call your number. **Recent calls** on the Twilio page shows each call's outcome.

Quick tunnels (such as `cloudflared tunnel --url` and free ngrok) usually get a
new address on every restart. Update the Public tunnel URL and the Twilio webhook
whenever the address changes. Otherwise, the signature check fails and calls are
rejected.

## Security model

- **Separate listener.** A tunnel running on your computer connects from
  `127.0.0.1`, and VoiceStudio's main API trusts local callers as you. The
  telephony listener is therefore a separate server that exposes only
  `/integrations/twilio/voice` and `/integrations/twilio/stream`. Every other
  path returns 404, including the API docs. **Never point a tunnel at the main
  backend port**, because that publishes the whole API.
- **Signed webhooks.** Every webhook must carry a valid `X-Twilio-Signature`.
  VoiceStudio uses your Auth Token to calculate Twilio's HMAC-SHA1 signature over
  the configured public URL and POST parameters. It also requires the request's
  `AccountSid` to match yours. Unsigned or wrongly signed requests get a plain 403.
  After 10 failures in a minute, the webhook answers 429 for the rest of that minute.
- **Per-call stream tokens.** Twilio does not sign the WebSocket upgrade. The
  webhook's TwiML therefore includes a random single-use token, valid for 60
  seconds and bound to that call's `CallSid`. The media stream closes with code
  1008 if its `start` message does not present the token.
- **Secrets stay local.** The Auth Token is encrypted in VoiceStudio's local
  settings store. It is never shown again, returned by the API, logged, or
  included in any export. **Remove Auth Token** deletes it.
- **Minimal call log.** Recent calls are kept in memory only and show the last
  four characters of the call ID, time, outcome and length of audio spoken.
  VoiceStudio does not store caller numbers or caller audio.
- **Off means off.** Turning the integration off stops the listener. Both public
  endpoints also check the setting on every request.

## Limits

| Limit | Default | Override |
|---|---|---|
| Simultaneous calls | 2 (more are rejected with a busy signal) | `OMNIVOICE_TWILIO_MAX_CALLS` (1–16) |
| Call length | 300 s, then the call is ended | `OMNIVOICE_TWILIO_MAX_CALL_SECONDS` (30–3600) |
| Webhooks per minute | 30 (more are rejected as busy) | `OMNIVOICE_TWILIO_WEBHOOKS_PER_MINUTE` |
| Listener port | 3950, then the next free port | `OMNIVOICE_TWILIO_PORT` |
| Listener address | `127.0.0.1` | `OMNIVOICE_TWILIO_HOST` (an IP address; anything else falls back to loopback) |
| Greeting length | 1,000 characters | — |

**Docker:** the listener binds to `127.0.0.1` inside the container. If your tunnel
runs in another container or on the host, set `OMNIVOICE_TWILIO_HOST=0.0.0.0`
and publish the port (for example, `-p 127.0.0.1:3950:3950`). Publish it only to
the tunnel, never to your LAN or the internet directly.

## How a call works

1. Twilio sends a signed `POST` to `/integrations/twilio/voice`. VoiceStudio
   replies with TwiML: `<Connect><Stream>` to `wss://<tunnel>/integrations/twilio/stream`,
   with the call's token as a custom parameter, followed by `<Hangup/>`.
2. Twilio opens the WebSocket and sends `connected` and `start`. VoiceStudio
   checks the token, then synthesizes the greeting sentence by sentence through
   the same pipeline as streaming TTS. Each sentence is resampled to 8 kHz mono,
   μ-law encoded and sent as 20 ms `media` frames (160 bytes each), so the first
   sentence plays while later ones are still being synthesized.
3. After the last frame, VoiceStudio sends a `mark`. Twilio echoes it once
   playback finishes. VoiceStudio then closes the stream, and Twilio follows the
   TwiML to `<Hangup/>`. A `stop` event (the caller hung up) ends the call early.

Synthesized audio carries VoiceStudio's usual provenance watermark when
watermarking is enabled.

## Troubleshooting

| Recent calls shows | Meaning |
|---|---|
| Rejected: invalid signature | The Auth Token, Public tunnel URL or Twilio webhook URL do not match. This is common after a quick tunnel restarts with a new address. |
| Rejected: invalid stream token | The stream started too late (over 60 s) or did not come from the call that received the TwiML. |
| Rejected: busy | Too many simultaneous calls or webhooks. Raise the limits above if needed. |
| Engine unavailable / Speech failed | The selected engine cannot run. Use **Test locally** to see the error. |
| No entry at all | The request did not reach VoiceStudio. Check that the tunnel is running, targets the tunnel target shown on the page, and that Twilio has the correct webhook URL. |

## Not included yet

This integration speaks a fixed greeting. It does not transcribe callers or carry
on a conversation. VoiceStudio has no built-in agent to decide replies
([agentic voice](../agentic-voice.md) describes using VoiceStudio as the voice
for your own agent). The call session is built so a conversational responder can
receive the caller's μ-law audio and speak replies later. Outbound calls, and
Plivo or Telnyx, are not supported. Their media-stream protocols are similar, so
the provider code is written to accommodate an adapter for them.
