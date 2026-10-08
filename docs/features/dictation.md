# Dictation

VoiceStudio dictation records from the system-wide shortcut, transcribes
locally, and—where the desktop permits it—inserts the result into the app where
the shortcut was pressed. The pill never needs keyboard focus.

Editor extensions, agent hooks, and scripts can stream their own microphone
audio to the versioned WebSocket API or transcribe files over HTTP. See the
[local speech platform](../speech-platform.md) for the protocol and examples.

## Use it

1. Install a dictation model from the Model Catalogue, then pick it in **Settings → Voice** or from the top-bar Engines menu (**Transcription → Sherpa-ONNX dictation → Dictation model**). The same choice is what the Sherpa-ONNX engine loads for dubbing and batch transcription.
2. Set the shortcut and hold/toggle behavior in **Settings → Hotkey**.
3. Put the cursor in a text field, press the shortcut, speak, then release or
   press again.

The **Transcriptions** page offers the same recorder as one contextual
**Start dictation** action: it appears in the empty state before the first
transcript and moves to the page header once history exists. A desktop start
wakes the recorder window before dispatch, so a hidden WebView cannot silently
miss the request. The in-app action confirms listener receipt, then resolves
only after microphone startup is accepted. Disabled, rejected, timed-out, or
failed starts are reported back on the page.

Whisper Tiny is the recommended default on macOS, Windows, and Linux. It
auto-detects more than 90 languages. Parakeet TDT v3 remains available for its
25 supported European languages, but it is not selected automatically.

The pill reports **Inserted** only after native delivery succeeds. **Copied**
means automatic insertion was unavailable and the complete final transcript is
ready for a normal paste. VoiceStudio retries a speech-level empty Sherpa decode
only through another ASR model whose weights are already installed; when that
fallback confirms the audio contains words, the silent model is demoted. This
recovery never starts a download.

## Destination and clipboard safety

The desktop captures the target at shortcut-down and carries its session ID
through partial, utterance, and summary messages. A late result from an older
session cannot use a newer session's target. macOS, Windows, and X11 validate
and reactivate the captured process/window before insertion. Wayland does not
expose a portable target identity or arbitrary foreign-window activation, so
the safe default leaves the transcript copied instead of guessing which app
should receive it. The GTK pill remains non-focusable.

For paste delivery, VoiceStudio snapshots text, HTML (with its plain-text
alternative), image, or file-list clipboard content, stages the transcript,
and restores the snapshot after the target consumes it.
Streaming segments share a generation-tracked lease: a stale restore cannot
win over a newer segment, and VoiceStudio never overwrites clipboard content
you copied during transcription. Unsupported clipboard formats cannot be
round-tripped; in that case the transcript remains on the clipboard instead of
attempting a lossy restore.

## Platform behavior

On macOS, native keyboard creation, layout lookup, paste and live typing run on
the main thread in both desktop shells. Session and clipboard work stays on its
existing worker path, and paste keeps the active keyboard layout's Command-V
mapping rather than assuming a physical QWERTY key.

| Platform | Automatic insertion |
| --- | --- |
| macOS | Reactivates the captured application and sends Command-V. Without Accessibility permission, the result stays copied. |
| Windows | Validates the captured window and process, requests foreground activation, then sends Ctrl-V. If Windows denies activation, the result stays copied. |
| Linux X11 | Reactivates the captured X11 window through EWMH, verifies it, then sends Ctrl-V. |
| Wayland | Leaves the complete transcript copied because a portable captured-window identity is unavailable. |
| Browser mode | Copies the transcript; browsers cannot target another desktop app. |

Advanced Wayland users can set `VOICESTUDIO_WAYLAND_UNTARGETED_INSERT=1` to
insert into whichever client owns keyboard focus when transcription finishes.
wlroots compositors use `wtype`; KDE Plasma and GNOME can use clipboard paste
through `dotool` or `ydotool`. Helpers run with host loader variables from an
AppImage, have a bounded timeout, and never retry after one may have emitted
partial input. This opt-in cannot promise the shortcut-down target if focus
changes. `dotool` needs direct write access to `/dev/uinput`; `ydotool` 1.0+
needs a running `ydotoold` with that access and a user-readable socket.
VoiceStudio checks these prerequisites before selection. Tray-started Wayland
dictation always stays copy-only.

### Transcriptions model setup

Transcriptions checks the active dictation model before enabling **Start dictation**. If weights are missing, the page lists every dictation model, grouped by the trade-off you are choosing between — best accuracy (offline, transcribes after you stop) versus lowest latency (streaming, live text while you speak) — with languages and download size on each row, so you install the one that fits your work rather than only the recommended default. A model already on disk can be switched to without a download. The chosen model's name, download size, and installation progress are shown. Downloads require an explicit click. Failed downloads can be retried, and model state refreshes when returning from Settings. Once installation is verified, Start dictation becomes available; recording never starts automatically. Existing transcription history remains accessible during setup.

### Floating recording controls

The recording bubble includes **Pause / Resume**, **Stop**, and **Close**. Pause disables microphone tracks, stops sending new audio, and freezes the elapsed recording timer while preserving the session. Resume continues the same session. Stop (or the recording shortcut) finishes and transcribes, including when paused. Close cancels pending recording/transcription and releases the microphone; text already delivered to another app cannot be retracted. Pausing is manual, not triggered by silence or desktop inactivity.

The Transcriptions page displays the effective recording shortcut and the platform paste shortcut. The floating bubble places its live transcript below the controls in a multiline preview, so controls cannot squeeze the text into a few characters.
