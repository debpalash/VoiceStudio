# Using Calls

The **Calls** workspace lets VoiceStudio phone someone for you, in your own
voice, and handle a short task: "book a table for 2 at 8pm Friday", "move my
appointment to next week", "ask what time you open on Sunday". You watch the
call live, can take over at any moment, and get a summary when it ends.

Calls go through **your own Twilio number**. Nothing is dialled until you
confirm a call, and nothing else leaves your machine. Twilio setup is covered
in [integrations/twilio.md](integrations/twilio.md); the API is described in
[integrations/calls.md](integrations/calls.md).

Open it from the sidebar: **Calls** (or press Ctrl/⌘ K and type "Calls").

## Before your first call

The workspace checks six things and shows a short checklist until all of them
pass. Each item has a **Fix** button that opens the right page:

| Check | What it means | Fix opens |
| --- | --- | --- |
| Twilio account connected | Account SID and auth token are saved | Integrations → Twilio |
| Public link for Twilio | Twilio can reach VoiceStudio through your tunnel | Integrations → Twilio |
| Phone number to call from | A Twilio number is set | Integrations → Twilio |
| Language model | A model is ready to hold the conversation | Settings → Models → LLM |
| Speech recognition | A speech-to-text model is ready | Settings → Models → ASR |
| A voice that may place calls | You have a voice allowed to call (below) | Saved voices |

Press **Check again** after fixing something. If the page says **Calls need the
latest backend**, your backend predates this feature: update VoiceStudio (or
restart the backend after updating), then set up Twilio.

## Which voices can call

Calls speak as you, so the voice picker only lists:

- a cloned voice you have **verified as your own** (Saved voices → **Edit
  voice profile** → **Voice ownership**, then record the consent statement), or
- a **designed** voice, which imitates nobody.

Clones of other people never appear in the picker. Use **Verify a voice** or
**Design a voice** under the picker to add one.

## Placing a call

1. **Phone number**: the full international number, starting with `+` and the
   country code (for example `+1 555 010 0199`). Spaces, dashes and brackets are
   fine. The field shows which country you are calling (when the country code
   belongs to one country; +1 and +7 are shared), or what is wrong with the
   number.
2. **What should it do?**: the task, in plain words. Include names, times and
   what is acceptable if the first choice is unavailable. The example chips
   fill in a starting point.
3. **Voice**: one of the voices above.
4. **What it will say first**: the AI disclosure line, on by default and
   prefilled from your template. Edit it for this call, or switch it off only
   where that is allowed.
5. **Maximum length**: the agent hangs up when this runs out.
6. Check the **Plan preview**: your task plus the guardrails the agent always
   follows. It will not share payment details, passwords or ID numbers, and it
   asks you when it is unsure.
7. Press **Call +…** and confirm. The call starts only after you confirm.

## During the call

- The **timeline** shows Dialing → Ringing → In call → Ended, next to the time
  used and the maximum.
- The **agent state** shows whether it is listening, thinking or speaking.
- The **transcript** fills in live; the other person's words appear while they
  are still speaking.
- **Take over** stops the agent from answering on its own. Type what to say
  next and press Enter; it is spoken in the selected voice.
- **Hang up** is always available and ends the call immediately.

If live updates drop, the workspace reconnects on its own and fills in anything
it missed; the call itself keeps going.

## After the call

The call shows an outcome (**Booked**, **Done**, **Not done**, **Needs you** or
**Failed**), a summary and the full transcript. **Copy summary** puts the
summary on the clipboard. **Call again** fills the form with the same task and
voice; the number is filled in only for calls placed in the current session,
because saved calls keep the number masked.

**Recent calls** lists your calls with their outcome, length and task. Select
one to open it.

## Incoming calls

**Incoming calls** (top of the workspace) chooses what happens when someone
calls your Twilio number:

- **Greeting**: play your greeting, then hang up.
- **AI agent**: talk with the caller in your voice, following the brief you
  write (for example "take a message: name, number and what it is about").

The number, credentials and greeting are set on
[Integrations → Twilio](integrations/twilio.md).

## Layout

The workspace adapts to the window: on wide screens (1440 px and up) recent
calls, the live call and the new-call form sit side by side; on medium screens
the call and the form share tabs next to recent calls; on narrow windows
everything is one column with tabs. Each pane scrolls on its own.
