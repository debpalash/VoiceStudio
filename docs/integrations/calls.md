# Call agent: phone calls in your voice

The call agent places a phone call, or answers one, and holds a short
conversation to get a task done. For example: "Book a table for 2 at 8pm Friday
under Palash." It speaks in your verified voice or a designed voice, listens to
the other person, and gives you a transcript, an outcome and a summary.

It builds on the [Twilio integration](twilio.md): the same Twilio account,
tunnel and telephony listener. Synthesis and speech recognition run on your
computer. The conversation text goes to the LLM you configured in
**Settings → LLM Providers**; choose a local provider (Ollama, LM Studio) to
keep it on your computer. Twilio carries the call audio.

## What you need

- The [Twilio integration](twilio.md) set up and turned on, with your tunnel
  running. Outbound calls are billed by Twilio. On a trial account, Twilio only
  lets you call numbers you have verified in the Twilio Console.
- Your Twilio phone number (the number calls come from).
- An LLM in **Settings → LLM Providers**.
- A speech recognition model (the dictation model, or the default Whisper
  model).
- A voice that may place calls: a profile you verified as your own voice, or a
  designed voice. Cloned voices of other people cannot place calls.

`GET /calls/readiness` returns this checklist, and the app shows it as guided
setup.

## Placing a call

Give the number (international format, such as `+14155550123`), the task brief,
and the voice. Optional: the engine, the language, a disclosure for this call
only, and a time limit (10 minutes by default, 30 at most).

1. VoiceStudio asks Twilio to place the call. Only one agent call (placed or
   answered) runs at a time by default (at most two, in call settings). There is no queue and no bulk
   dialing: every call starts from your own request.
2. When the call is answered, the agent speaks the disclosure first. This line
   cannot be interrupted.
3. The agent states the request. It listens, detects when the other person has
   finished speaking, transcribes it, and replies. Synthesis starts on the first
   sentence of the reply to keep pauses short.
4. If the other person talks over the agent, the agent stops speaking and
   listens.
5. The agent ends the call politely when the task is done or cannot be done.
   It escalates to you ("needs you") when asked something the brief does not
   answer.
6. After the call, VoiceStudio writes a summary and outcome: `booked`, `done`,
   `not_done`, `needs_you` or `failed`.

While the call runs you can watch the live transcript. You can also take over:
pause the agent and type exactly what it should say. You can hang up at any time.

## AI disclosure

By default every call opens with:

> Hi, this is {name}'s AI assistant calling on their behalf.

`{name}` is the name in call settings, or the voice profile's name if none is
set. You can edit this text in call settings, and override it for a single
call. **You can also leave it empty. That is your decision and your
responsibility.** Many places require callers to say that a call uses an
artificial or AI voice (see [Legal note](#legal-note)).

Regardless of the disclosure text, the agent is instructed to say truthfully
that it is an AI assistant if someone asks.

## Safeguards

- **Your own or a designed voice only.** Outbound calls refuse any profile that
  is not verified as your own voice and is not a designed voice (HTTP 403,
  `voice_not_allowed`).
- **One call per request.** No queues, lists or automatic redialing. The number
  of simultaneous agent calls, placed or answered, is limited to 1 by default
  (2 at most). Incoming calls beyond the limit get a busy signal.
- **Stays on task.** The agent only pursues the brief and declines unrelated
  requests. It never invents facts about you beyond the brief.
- **No payment or ID numbers.** The agent is told never to give card, bank,
  password or government-ID numbers. A separate check blocks any sentence
  containing a card number, or a number of 9 or more digits that is not in your
  brief (digits separated by spaces, dashes, dots or commas included). The agent says "I'm not able to share that number over the phone"
  instead.
- **Time limit.** Calls end after their time limit (Twilio also enforces it).
- **Recording is off.** See [Recording](#recording).

## Recording

Calls are not recorded by default. To record, turn on **Record calls** in call
settings **and** use a disclosure that says the call is recorded, for example
"Hi, this is Palash's AI assistant, and this call is recorded." Phrases such as
"is recorded", "may be recorded" or "we're recording this call" count; a negated
one ("this call is not recorded") does not. Without such a notice the call is
not recorded, even with the setting on.
Recordings are stereo WAV files (the other person on the left, the agent on the
right) stored in your data folder under `calls/`. They are never uploaded.

## Incoming calls

In call settings, **Answer incoming calls with** chooses between:

- `greeting` (default): the fixed greeting from the Twilio page, as before.
- `agent`: the agent answers, speaks the disclosure, and follows the
  **incoming-call brief** (for example, "Take a message: ask who is calling and
  what it is about"). It uses the voice, engine and language from the Twilio
  page. If no LLM is available, the greeting answers instead when one is
  configured; otherwise the call is rejected with a busy signal.

## Legal note

This is not legal advice. Laws on automated and AI-voice calls differ by country
and state, and you are responsible for following them. Examples:

- In the United States, the FCC treats AI-generated voices as "artificial"
  voices under the TCPA. Calls with them need the called party's prior consent
  in many cases, and several states require disclosure that a call is automated.
- Many jurisdictions require **all parties** to consent before a call is
  recorded. The recording rule above (the disclosure must say the call is recorded) helps,
  but it does not replace consent where consent is required.
- Telemarketing, robocall and do-not-call rules can apply even to a single call.

Use the call agent for ordinary personal tasks you would make yourself, such as
bookings, appointments and questions to a business. Do not use it to impersonate
anyone.

## Privacy

- Transcripts, summaries and full phone numbers are stored only in your local
  database. The API returns numbers masked (`+1••••••0123`).
- **Delete** removes a call's record and recording.
- The conversation text is sent to your configured LLM provider. With a local
  provider, nothing but the call audio leaves your computer.

## API

All routes are on VoiceStudio's local API (admin-only; never on the telephony
listener or tunnel).

| Method and path | Purpose |
|---|---|
| `POST /calls` | Place a call. Body: `{to, brief, profile_id, engine?, language?, disclosure?, max_minutes?}`. Returns `201 {call: CallRecord}`. |
| `GET /calls?limit=` | `{calls: [CallRecord]}`, newest first. |
| `GET /calls/{id}` | `CallRecord` with `transcript` and `timeline`. |
| `GET /calls/{id}/events` | Server-sent events until the call ends (below). |
| `POST /calls/{id}/say` | `{text}`: speak exactly this next. |
| `POST /calls/{id}/takeover` | `{enabled}`: pause the LLM. Only `/say` speaks while enabled. |
| `POST /calls/{id}/hangup` | End the call now (cancels it if still ringing). |
| `DELETE /calls/{id}` | Delete a finished call's record and recording. |
| `GET /calls/{id}/recording` | The WAV recording, if the call was recorded. |
| `GET /calls/settings`, `PUT /calls/settings` | `{from_number, disclosure_template, user_name, inbound_mode, inbound_brief, max_concurrent, record_calls}`. `PUT` changes only the fields sent. |
| `GET /calls/readiness` | `[{id, ok, detail}]` for `credentials`, `tunnel`, `number`, `llm`, `asr`, `voice`. |

**CallRecord:** `{id, direction, to_masked, status, created_at, started_at,
ended_at, duration_s, profile_id, brief, outcome, summary, disclosure, takeover,
agent_state, recording, error, transcript?, timeline?}`. For an incoming call,
`to_masked` is the caller's number. `status` is `queued`, `initiated`,
`ringing`, `in_progress`, `completed`, `busy`, `no_answer`, `failed` or
`canceled`. Transcript turns are `{speaker: "agent" | "caller", text, t}`, where
`t` is seconds since the call was answered. The optional `source` is
`disclosure` or `manual` (spoken with `/say`), and `interrupted: true` marks a
turn the other person talked over.

**Events** (`data: {json}` lines):

- `{type: "status", status, t}`
- `{type: "transcript", speaker, text, final, t}`: agent turns stream as
  `final: false` while spoken, then arrive once with `final: true`.
- `{type: "agent_state", state}`: `listening`, `thinking` or `speaking`.
- `{type: "outcome", outcome, summary}`
- `{type: "ended", call: CallRecord}`: the last event; the stream closes after it.

**Errors** carry `detail: {code, message}`: `invalid_number`, `missing_brief`
(400); `voice_not_allowed` (403); `profile_not_found` (404); `not_configured`,
`integration_disabled`, `listener_not_running`, `missing_from_number`,
`llm_unavailable`, `busy`, `not_connected`, `call_ended` (409);
`twilio_error` (502, with Twilio's message and `twilio_code`).

## How it works

- `POST /calls` calls Twilio's REST API (`Calls.json`) with your Account SID and
  Auth Token. `Url` is the telephony listener's voice webhook with the call's id
  (`?call=`), and `StatusCallback` is `/integrations/twilio/status`. Twilio
  signs both, and VoiceStudio checks the signature exactly as for incoming calls.
- When the call is answered, the webhook answers with a `<Connect><Stream>`
  whose single-use token is bound to both the Twilio call and the agent session.
- Caller audio (8 kHz μ-law) is split into utterances by an energy-based voice
  activity detector (700 ms of silence ends one), upsampled to 16 kHz and
  transcribed by the dictation/capture speech engine.
- The LLM receives the brief, the disclosure, the guardrails and the
  conversation. It replies with `SAY:`, `ACTION: none | end_call | escalate` and
  an optional `OUTCOME:` line, streamed so the first sentence is synthesized
  while the rest is written.
- Replies are synthesized with the streaming TTS pipeline in the chosen voice,
  resampled to 8 kHz μ-law and sent as 20 ms frames. On barge-in (about 200 ms
  of the other person's speech while the agent talks), VoiceStudio sends Twilio
  a `clear` to drop buffered audio.
