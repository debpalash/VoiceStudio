# Workflows and Calls

The **Workflows** sidebar opens a local canvas with executable audio and text recipes and
call drafts. Steps reuse VoiceStudio’s speech, transcription, translation and
voice-conversion APIs and saved voices.

## Run a speech workflow

1. Choose **New from template** under the workflow list, then **Batch narration**
   or its **Normalize audio** variant.
2. Select Start. Paste scripts separated by a line containing `---`, or import
   TXT, Markdown, DOC/DOCX, PDF or EPUB files. Each file becomes one clip.
   Runs accept up to 50 nonempty scripts of 20,000 characters each.
3. Select Speak. Choose a saved voice, language and speed. Install the selected
   engine explicitly in Settings if it is not ready.
4. Choose **Run workflow**, then run from the output panel. Steps still missing a
   setting show **Needs setup**, and Run stays unavailable until they are set;
   the note beside Run opens the first one. Preview and export
   the resulting WAV clips with numbered, filesystem-safe names.

Speech goes through the normal generation endpoint, including mastering,
normalization and synthetic marking. The optional Normalize audio step applies
adjustable peak normalization (−24 to −1 dBFS, default −2) with a silence guard and re-marks synthetic audio;
it does not download a cleanup model or silently substitute unprocessed audio.
It is peak normalization, not a LUFS loudness target.

## Audio and text recipes

- **Audio to transcript**: import audio → Transcribe → export TXT.
- **Change the voice**: import audio → Convert with a saved voice → export WAV.
- **Translate and narrate**: import audio → Transcribe → Translate → Speak → export WAV.

Audio inputs accept up to 50 files, each no larger than 64 MiB. Imported files
stay in local browser storage; removing the last workflow reference deletes
the stored source and invalidates its cached run. Pending deletions are saved
with the draft and retried after reopening if storage temporarily fails; the original files are never modified. Transcription
automatically detects the spoken language and uses the installed ASR engine
without downloading word-alignment models; a transcription-only workflow does not need TTS.

Translation uses an explicitly selected local Argos or NLLB engine. Select the
source and target languages and install the model or language pair in Settings
before running. Workflows never install models or switch to a cloud translator
on their own. Select Speak or Convert to choose the output voice.

Steps have typed inputs: Speak consumes text; Transcribe consumes audio;
Translate consumes text; Convert consumes audio. Normalize audio accepts only
generated or converted speech, preserving the synthetic-audio marking contract.
Connect these steps into one path ending at End. Cycles, branches, disconnected
steps, incompatible types and missing voices are rejected before inference.
Agent, Condition and Call remain clearly labelled design-only steps.

Execution is serial, with per-clip and per-step status. Each completed text or audio
step is checkpointed in local IndexedDB. Cancel or leave the page to stop
scheduling work; Retry reuses saved outputs and resumes the interrupted or failed
step. In-flight backend work may finish after the client disconnects. A process
interrupted before its checkpoint may need that step regenerated. Reopening
never runs a workflow automatically.

Each workflow retains its latest run, including playable audio or readable text, until another
run replaces it or the workflow is deleted. Export files you want to keep.
Changing source files, scripts, voice, languages, translation provider, speed,
normalization target, graph order or active engine/model
starts a fresh run; moving nodes or renaming the workflow does not invalidate
completed audio. The output panel identifies results from changed settings.
Closing the app stops execution; this is not a background scheduler. Storage
failure stops the run rather than claiming that recovery is available.

## Call drafts

Restaurant booking, appointment rescheduling and opening-hours templates create
a Start → Call → End draft. **Prepare call** fills the destination and brief in
the Calls form. Readiness checks, voice ownership, disclosure and final
confirmation still apply; the workflow runner never dials. Use **Calls** to view
call history or start a call independently.

Pan, zoom and use the minimap to navigate the canvas. Arrow keys move selected
steps. Drafts save locally and can be duplicated or deleted. Keyboard moves persist
just like dragging; reopening preserves all saved workflows and steps.

The **Calls** view lets VoiceStudio phone someone for you, in your own
voice, and handle a short task: "book a table for 2 at 8pm Friday", "move my
appointment to next week", "ask what time you open on Sunday". You watch the
call live, can take over at any moment, and get a summary when it ends.

Calls go through **your own Twilio number**. Nothing is dialled until you
confirm a call, and nothing else leaves your machine. Twilio setup is covered
in [integrations/twilio.md](integrations/twilio.md); the API is described in
[integrations/calls.md](integrations/calls.md).

Open **Workflows** from the sidebar (or press Ctrl/⌘ K and type
"Workflows"), then choose **Calls**, or type "Calls" in Ctrl/⌘ K to go there
directly.

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
