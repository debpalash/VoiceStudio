# Twilio: answer phone calls with a saved voice

**Integrations → Twilio** answers calls to your Twilio phone number in one of
your saved voices. When someone calls, VoiceStudio speaks your greeting over a
[Twilio Media Stream](https://www.twilio.com/docs/voice/media-streams) and then
hangs up. Synthesis runs on your computer with your chosen voice and engine.
Twilio receives the 8 kHz phone audio it plays to the caller.

The same setup also runs the [call agent](calls.md), which places calls and
holds a conversation in your voice, and can answer incoming calls instead of the
greeting.

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

Open **Integrations → Twilio**. The page is a guided checklist: each step shows
**To do** or **Done**, the header shows the overall status (**Not set up**,
**Ready**, **Live**), and **Readiness** in the side panel jumps to whatever is
left. Each step saves on its own.

1. **Twilio account.** Enter your **Account SID** and **Auth Token** from the
   Twilio Console home page and choose **Save and check**. VoiceStudio checks the
   format; Twilio confirms the token when it signs your first call. A saved token
   is never shown again: use **Replace** or **Remove**.
2. **Public tunnel.** Pick cloudflared or ngrok. The page shows the install
   command for your operating system and the exact command to start the tunnel
   against the call gateway's port (**3950** unless you set
   `OMNIVOICE_TWILIO_PORT`), **not** VoiceStudio's main port:

   ```sh
   cloudflared tunnel --url http://127.0.0.1:3950
   # or
   ngrok http 127.0.0.1:3950
   ```

   Paste the tunnel's `https://…` address into **Public tunnel URL** and save.
   Use only the origin, without a path. **Check** shows whether the gateway is
   listening; it starts when you turn on calls. If port 3950 was taken, the
   gateway uses the next free port and the commands update; restart the tunnel
   with the new command.
3. **Phone number.** Copy the **Voice webhook URL**
   (`https://<your-tunnel>/integrations/twilio/voice`). In the Twilio Console,
   open **Phone Numbers → Manage → Active numbers**, select your number, and under
   **Voice configuration** set **A call comes in** to **Webhook**, that URL, and
   **HTTP POST**. Save. The step shows **Done** once a call passes the
   signature check (rejected or busy attempts do not count).
4. **Voice and behavior.** Choose a voice (**Default voice** uses the engine's
   default; voices marked **Can call** are your verified own voice or a designed
   voice, the ones the [call agent](calls.md) may use) and an engine (**Active
   engine** follows your current engine). Choose what answers incoming calls:
   **Play greeting** (write the greeting, up to 1,000 characters) or **AI agent**
   (the [call agent](calls.md); the greeting can then stay empty). The AI
   disclosure the agent opens with is editable here; without the agent,
   **Add to greeting** inserts a short one. Some places require telling callers
   they hear an AI voice.
5. **Test.** **Play phone-quality preview** resamples the greeting to 8 kHz,
   μ-law encodes and decodes it, exactly as a caller hears it. This runs entirely
   on your computer and pre-renders the greeting, so the first real call starts
   speaking immediately. When the button is unavailable, the reason is shown
   below it.
6. Choose **Turn on calls** in the header. Call your number; **Recent calls**
   shows each call's outcome.

Quick tunnels (such as `cloudflared tunnel --url` and free ngrok) usually get a
new address on every restart. Update the Public tunnel URL and the Twilio webhook
whenever the address changes. Otherwise, the signature check fails and calls are
rejected.

## Security model

- **Separate listener.** A tunnel running on your computer connects from
  `127.0.0.1`, and VoiceStudio's main API trusts local callers as you. The
  telephony listener is therefore a separate server that exposes only
  `/integrations/twilio/voice`, `/integrations/twilio/stream` and (for calls
  the [call agent](calls.md) places) `/integrations/twilio/status`. Every other
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
  included in any export. **Remove** deletes it.
- **Minimal call log.** Greeting calls are kept in memory only and show the last
  four characters of the call ID, time, outcome and length of audio spoken.
  VoiceStudio does not store their caller numbers or caller audio. Calls handled
  by the [call agent](calls.md) keep a local record with a transcript (see its
  Privacy section).
- **Off means off.** Turning the integration off stops the listener. Every public
  endpoint also checks the setting on every request.

## Limits

| Limit | Default | Override |
|---|---|---|
| Simultaneous calls | 2 (more are rejected with a busy signal) | `OMNIVOICE_TWILIO_MAX_CALLS` (1–16) |
| Call length | 300 s, then the call is ended (calls the agent places use their own limit, up to 30 minutes) | `OMNIVOICE_TWILIO_MAX_CALL_SECONDS` (30–3600) |
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
| Engine unavailable / Speech failed | The selected engine cannot run. Use **Play phone-quality preview** to see the error. |
| No entry at all | The request did not reach VoiceStudio. Check that the tunnel is running, uses the exact tunnel command shown in the **Public tunnel** step, and that Twilio has the correct webhook URL. |

## Not included yet

Conversations and outbound calls are handled by the [call agent](calls.md). To
use VoiceStudio as the voice of your own agent instead, see
[agentic voice](../agentic-voice.md). Plivo and Telnyx are not supported. Their
media-stream protocols are similar, so the provider code is written to
accommodate an adapter for them.
