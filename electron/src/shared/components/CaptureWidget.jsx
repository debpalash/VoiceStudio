import React, { useCallback, useEffect, useRef, useState } from 'react';
import { copyText } from '../utils/copyText';
import { X, Loader, Pause, Play, Square } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { useAppStore } from '../store';
import { useTranslation } from 'react-i18next';
import { invoke as tauriInvoke } from '@tauri-apps/api/core';

import { API, apiFetch } from '../api/client';
import { authenticatedWsUrl } from '../api/authSession';
import { addTranscription } from '../pages/Transcriptions';
import { describeMicError, detectPlatform, micErrorMessage, micHintKey } from '../utils/micError';
import { checkMicrophone, openMicrophoneSettings } from '../utils/permissions';
import { showMicDeniedGuide } from '../utils/micDeniedToast';
import { asrMissingPayload, toastAsrModelMissing } from '../utils/asrModelMissing';
import { createWaveform } from './captureWaveform';
import { emitDictationNotice } from '../utils/dictationNotice';
import { audioFormatForMimeType, startSupportedMediaRecorder } from '../utils/mediaRecorder';
import { BROWSER_DICTATION_REQUEST } from '../utils/dictationCapture';

// True inside the Tauri shell (desktop app / widget window); false in the
// browser webui / Docker, where the native commands don't exist. Gating on
// this keeps "not in Tauri" out of the error paths entirely — a failure that
// happens INSIDE Tauri is real and must surface, never be swallowed.
function inTauri() {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

// Flip the system tray icon between default and red-dot.
async function setTrayRecording(recording) {
  if (!inTauri()) return; // browser webui / Docker — no tray
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('set_tray_recording', { recording });
  } catch (err) {
    // Cosmetic only (the tray dot) — a pill error would outrank the failure.
    console.warn('set_tray_recording failed:', err);
  }
}

// Show the standalone pill window, bottom-centred and without taking focus
// (no-op in the browser webui, where the pill is just a DOM node).
async function showWidgetWindow() {
  if (!inTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('show_dictation_pill');
  } catch (err) {
    // Dictation still records and pastes without the pill on screen — the
    // session must not be aborted over its chrome.
    console.warn('widget show failed:', err);
  }
}

// Hide the standalone widget window (no-op in the browser webui).
async function hideWidgetWindow() {
  if (!inTauri()) return;
  try {
    const { getCurrentWindow } = await import('@tauri-apps/api/window');
    await getCurrentWindow().hide();
  } catch (err) {
    console.warn('widget hide failed:', err);
  }
}

// macOS Accessibility probe (AXIsProcessTrusted via the shell). Resolves true
// on Windows/Linux and outside Tauri — there is nothing to grant there.
async function checkAccessibility() {
  if (!inTauri()) return true;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    return (await invoke('check_accessibility')) !== false;
  } catch (err) {
    // Older shell without the command — don't block dictation on the probe.
    console.warn('check_accessibility failed:', err);
    return true;
  }
}

// Open the OS pane where the user grants Accessibility (macOS
// Privacy_Accessibility; no-op elsewhere — the shell command handles the OS
// switch).
async function openA11ySettings() {
  if (!inTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('open_accessibility_settings');
  } catch (err) {
    console.warn('open_accessibility_settings failed:', err);
  }
}

const LS_CAPTURE_MODE = 'omni_capture_mode';
// Live retract-retype (word-by-word typing with visible backspace corrections
// in the target app) is OPT-IN: default dictation only inserts committed
// finals. Set '1' to re-enable the live mode.
const LS_LIVE_TYPING = 'omni_capture_live_typing';

// How many waveform bars the pill draws while recording.
const WAVE_BARS = 12;

// How long an error pill that has NO transcript to rescue stays up before it
// dismisses itself. Long enough to read the message, short enough that a
// failed session can't leave the widget parked on screen indefinitely.
const ERROR_AUTO_DISMISS_MS = 8000;

// How long the widget window may stay visible while the pill is idle before we
// treat it as stranded and hide it. Long enough that the normal show → emit →
// startRecording() handshake (a few frames) is never interrupted, short enough
// that a user who sees an empty capsule does not have to live with it.
const IDLE_VISIBLE_GRACE_MS = 1200;

// How often we re-check that invariant while idle. The window is shown by the
// Rust side, so there is no React state change to key off when a press is
// dropped — only polling catches a window that became visible while we were
// already idle. One `isVisible()` IPC per tick, skipped entirely whenever the
// document reports itself hidden (the overwhelmingly common case).
const IDLE_VISIBLE_POLL_MS = 600;

// The widget window deliberately does not take focus, so it cannot rely on
// the main window's focus-based permission refresh after System Settings.
// Reconcile only while the Accessibility blocker is visible.
const A11Y_SETUP_RECHECK_MS = 1000;

// How long the Accessibility prompt may hold the always-on-top pill on screen.
//
// The pill is created always-on-top, and the setup state had no time limit at
// all: it sat above every application, including the first-run setup window it
// was covering, until Accessibility was granted or the user dismissed it by
// hand (#1845, #1886). A permission the user has not granted yet is not urgent
// enough to outrank whatever they are actually doing, and on a clean install
// they are usually mid-setup and cannot grant it yet anyway.
//
// Polling does NOT stop when the window hides. The check keeps running, so
// granting Accessibility later still returns the widget to idle on its own —
// what expires is the pill's claim on the screen, not the reconciliation.
const A11Y_SETUP_VISIBLE_MS = 20_000;

// A dictation model id is a sherpa-onnx live model when it carries the
// `sherpa-` prefix the backend assigns (see services/sherpa_dictation.py). Only
// then do we open the low-latency raw-PCM streaming path. Other models use a
// supported MediaRecorder container when available, or raw PCM on WebKitGTK.
export function isSherpaModel(id) {
  return typeof id === 'string' && id.startsWith('sherpa-');
}

/** Classify the backend's explicit sherpa final-frame contract. */
export function classifySherpaFinal(message) {
  const text = typeof message?.text === 'string' ? message.text.trim() : '';
  if (message?.final_kind === 'summary') return text ? 'summary' : 'terminator';
  if (message?.final_kind === 'utterance') return text ? 'utterance' : 'ignore';
  return 'ignore';
}

/** Return the EOF-summary suffix that has not already been committed live. */
export function sherpaSummaryTail(summaryText, committed) {
  const summary = (summaryText || '').trim();
  const delivered = (committed || []).join(' ').trim();
  if (!delivered) return summary;
  if (summary === delivered) return '';
  const prefix = `${delivered} `;
  return summary.startsWith(prefix) ? summary.slice(prefix.length).trim() : '';
}

/** Combine delivery outcomes without ever hiding a clipboard-only fallback. */
export function aggregateDeliveryKind(current, next) {
  const priority = { noop: 0, pasted: 1, inserted: 2, copied: 3 };
  if (!current) return next || null;
  if (!next) return current;
  return (priority[next] || 0) > (priority[current] || 0) ? next : current;
}

/**
 * Compute the keystroke delta to turn `prevTyped` (what we've already typed into
 * the focused field for the in-flight utterance) into `nextText` (the recognizer's
 * latest revision of that same utterance). Pure + exported for unit testing.
 *
 * Streaming recognizers don't only append — they REVISE earlier words ("recognise"
 * → "recognize", "to" → "two"). So we find the longest common prefix, retract
 * everything after it with backspaces, then type the corrected suffix. The common
 * case (pure append) yields `backspaces: 0` and just the new tail.
 *
 *   computeTypeDelta('hello wor', 'hello world') → { backspaces: 0, text: 'ld' }
 *   computeTypeDelta('hello to', 'hello two')    → { backspaces: 1, text: 'wo' }
 *   computeTypeDelta('hello', 'hello')           → { backspaces: 0, text: '' }  (noop)
 *
 * Returns `{ backspaces, text }`; `noop` is true when both are empty.
 */
export function computeTypeDelta(prevTyped, nextText) {
  const prev = prevTyped || '';
  const next = nextText || '';
  // Longest common prefix (by UTF-16 code unit — enigo types code points but the
  // backspace count we send is per-character; spread to count code points so an
  // astral char like an emoji retracts/types as one unit on every platform).
  const prevChars = Array.from(prev);
  const nextChars = Array.from(next);
  let i = 0;
  const max = Math.min(prevChars.length, nextChars.length);
  while (i < max && prevChars[i] === nextChars[i]) i++;
  const backspaces = prevChars.length - i;
  const text = nextChars.slice(i).join('');
  return { backspaces, text, noop: backspaces === 0 && text === '' };
}

/**
 * Map a failed `simulate_paste`/`simulate_type` invoke into an actionable
 * `{ kind, message }`. The Rust command prefixes its Err strings with the
 * failing layer — "a11y:" (macOS Accessibility not granted; the pill offers
 * open_accessibility_settings), "clipboard:" (couldn't write/restore the user
 * clipboard), "preflight:" (input was rejected before any key could be emitted)
 * or "paste:" (the synthetic ⌘V/Ctrl+V itself failed). Pure + exported for
 * unit testing.
 */
export function parsePasteError(err) {
  const raw = typeof err === 'string' ? err : (err && err.message) || String(err ?? '');
  for (const kind of ['a11y', 'clipboard', 'paste', 'preflight']) {
    if (raw.startsWith(`${kind}:`)) return { kind, message: raw.slice(kind.length + 1).trim() };
  }
  return { kind: 'paste', message: raw };
}

// Native sessions own clipboard preservation and the captured focus target,
// so the widget must never pre-write the WebView clipboard in Tauri.
async function deliverText(text, sessionId = null) {
  if (!inTauri()) {
    let copyErr = null;
    try {
      await copyText(text);
    } catch (err) {
      copyErr = err;
    }
    if (copyErr) {
      return {
        ok: false,
        error: { kind: 'clipboard', message: String(copyErr?.message || copyErr) },
      };
    }
    return { ok: true, kind: 'copied', copySource: 'webview' };
  }
  if (!sessionId) {
    return {
      ok: false,
      error: { kind: 'paste', message: 'native output session unavailable' },
    };
  }
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    const outcome = await invoke('simulate_paste', { text, sessionId });
    return {
      ok: true,
      kind: outcome === 'copied' || outcome === 'inserted' ? outcome : 'pasted',
      copySource: outcome === 'copied' ? 'native' : null,
    };
  } catch (err) {
    const nativeError = parsePasteError(err);
    if (nativeError.kind === 'clipboard') {
      try {
        // Linux WebKit and the native clipboard backend do not always support
        // the same display/session combinations. Try the WebView path only
        // after native delivery has failed, preserving the captured target.
        await copyText(text);
        return { ok: true, kind: 'copied', copySource: 'webview' };
      } catch (copyErr) {
        return {
          ok: false,
          error: { kind: 'clipboard', message: String(copyErr?.message || copyErr) },
        };
      }
    }
    return { ok: false, error: nativeError };
  }
}

// Preserve an authoritative transcript without emitting keyboard input. This
// is the only safe rescue after live synthesis may have left a partial prefix.
async function copySessionText(text, sessionId) {
  if (!inTauri() || !sessionId) {
    return {
      ok: false,
      error: { kind: 'clipboard', message: 'native output session unavailable' },
    };
  }
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    const outcome = await invoke('copy_dictation_output_session', { text, sessionId });
    if (outcome !== 'copied') {
      return {
        ok: false,
        error: { kind: 'clipboard', message: 'native clipboard delivery was not confirmed' },
      };
    }
    return { ok: true, kind: 'copied', copySource: 'native' };
  } catch (err) {
    const nativeError = parsePasteError(err);
    if (nativeError.kind === 'clipboard') {
      try {
        await copyText(text);
        return { ok: true, kind: 'copied', copySource: 'webview' };
      } catch (copyErr) {
        return {
          ok: false,
          error: { kind: 'clipboard', message: String(copyErr?.message || copyErr) },
        };
      }
    }
    return { ok: false, error: nativeError };
  }
}

// Deliver a committed utterance to the session's captured target so each
// silence endpoint lands as the user pauses. Returns the native outcome so the
// session can surface a failed segment instead of pretending it landed.
async function pasteSegment(text, sessionId) {
  if (!text) return { ok: true, kind: 'noop' };
  return deliverText(text, sessionId);
}

// Live, word-by-word typing of the in-flight utterance into the session's
// captured target (words appear AS you speak, not only on pauses). Given the
// delta vs what we last typed, it backspaces any revised tail then types the
// corrected suffix via the `simulate_type` Tauri command (one round trip). The
// structured outcome lets the caller latch failures; native synthesis can
// partially emit before returning Err, so retry-pasting the whole utterance
// would not be safe.
async function typeDelta({ backspaces, text }, sessionId) {
  if (!backspaces && !text) return { ok: true, kind: 'noop', mayHaveEmitted: false };
  if (!inTauri() || !sessionId) {
    return {
      ok: false,
      error: { kind: 'paste', message: 'native output session unavailable' },
      mayHaveEmitted: false,
    };
  }
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('simulate_type', { text, backspaces, sessionId });
    return { ok: true, kind: 'inserted', mayHaveEmitted: true };
  } catch (err) {
    console.warn('simulate_type failed:', err);
    const error = parsePasteError(err);
    return {
      ok: false,
      error,
      // Native marks failures detected before synthesis separately. Retrying
      // those via committed delivery is safe; all other failures may have
      // emitted a partial delta before the helper returned Err.
      mayHaveEmitted: error.kind !== 'preflight' && error.kind !== 'a11y',
    };
  }
}

function formatElapsed(ms) {
  const secs = Math.floor(ms / 1000);
  const mins = Math.floor(secs / 60);
  const s = secs % 60;
  if (mins > 0) return `${mins}:${String(s).padStart(2, '0')}`;
  return `${s}s`;
}

// Localized headline for the pill's error state.
function errorLabel(t, info) {
  switch (info?.kind) {
    case 'a11y':
      return t('capture.a11y_error');
    case 'clipboard':
      return t('capture.clipboard_error');
    case 'paste':
    case 'preflight':
      return t('capture.paste_error');
    case 'mic':
      return t('capture.mic_denied');
    case 'asr_missing':
      // Not "Transcription failed: …" — nothing was transcribed or failed; a
      // model is absent, and the fix is to install one.
      return t('asr_missing.message');
    default:
      return t('capture.transcription_failed', { message: info?.message || '' });
  }
}

/**
 * CaptureWidget — floating pill for dictation.
 *
 * Minimal status-only UI: live waveform (or status dot) + label + timer.
 * All interaction via global hotkey (hold-to-talk); Esc cancels anywhere.
 * Records → transcribes → delivers → auto-dismisses — and every state the pill
 * shows is TRUE: "Inserted" only after native synthesis succeeds, "Copied"
 * after clipboard fallback, model progress from backend status frames, and an
 * actionable setup state when macOS Accessibility has not been granted yet.
 */
export default function CaptureWidget({ onDismiss }) {
  const { t } = useTranslation();
  const [state, setState] = useState('idle'); // idle | setup | recording | transcribing | done | error
  const [transcript, setTranscript] = useState('');
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(false);
  const [duration, setDuration] = useState(0);
  const [captureMode] = useState(() => localStorage.getItem(LS_CAPTURE_MODE) || 'fast');
  const [, setLastEngine] = useState('');
  const [, setLastTime] = useState(0);
  const [partialText, setPartialText] = useState('');
  // How the finished transcript actually reached the user: 'inserted' after
  // confirmed native synthesis, 'copied' for clipboard-only delivery, or the
  // legacy browser 'pasted' value. Drives the done label truthfully.
  const [doneKind, setDoneKind] = useState(null);
  // { kind, message } for the error state (mic / a11y / clipboard / paste /
  // transcription / server). The a11y kind renders the Open-Settings action.
  const [errorInfo, setErrorInfo] = useState(null);
  // Backend model lifecycle ({type:"status"} WS frames): null when ready, else
  // { stage: 'downloading' | 'loading', progress: 0..1 | null }.
  const [modelStatus, setModelStatus] = useState(null);
  // Live waveform bars (0..1 heights). Only fed on the raw-PCM paths where the
  // micCapture AudioWorklet already emits frames — no second audio pipeline.
  const [bars, setBars] = useState(() => Array.from({ length: WAVE_BARS }, () => 0));
  const [waveOn, setWaveOn] = useState(false);

  // Live-dictation prefs (mirrored from the backend dictation.* namespace).
  // `mode` switches the hotkey start/stop semantics; `modelId` selects the
  // sherpa-onnx live engine; `enabled` gates the hotkey entirely.
  const dictationEnabled = useAppStore((s) => s.dictationEnabled);
  const dictationMode = useAppStore((s) => s.dictationMode);
  const loadDictationPrefs = useAppStore((s) => s.loadDictationPrefs);
  // Mode/enabled are also read through refs inside event listeners so the
  // long-lived tray/keyboard handlers always see the current value without
  // re-subscribing on every pref change.
  const modeRef = useRef(dictationMode);
  const enabledRef = useRef(dictationEnabled);
  /** @type {React.MutableRefObject<null | { pending: boolean, ok: boolean, promise: Promise<void> | null }>} */
  const prefsHydrationRef = useRef(null);
  useEffect(() => {
    modeRef.current = dictationMode;
  }, [dictationMode]);
  useEffect(() => {
    enabledRef.current = dictationEnabled;
  }, [dictationEnabled]);
  // Load the persisted dictation prefs into THIS window's store.
  //
  // The widget is its own window with its own store, created at app start —
  // usually before the backend is listening. It used to hydrate exactly once
  // and memoize the promise whether or not the load worked, so a startup
  // failure pinned the store's seed model (`sherpa-whisper-tiny`) for the life
  // of the app. The main window, which loaded fine, checked the model the user
  // actually picked and said "ready"; the widget then asked the server for the
  // seed, which was not installed, and the pill reported "No speech-to-text
  // model is installed" with Parakeet sitting on disk. Nothing in the main
  // window could tell, because the loader marked itself loaded either way.
  //
  // So: only a load the backend actually answered is kept. A failed one is
  // retried by the next caller. And a capture start passes `fresh`, which
  // re-reads even after a success — the model can be changed from the main
  // window (Transcriptions, Settings) and this store is not the one that
  // changed. An in-flight load is always shared rather than duplicated.
  const ensureDictationPrefsHydrated = useCallback(
    ({ fresh = false } = {}) => {
      const current = prefsHydrationRef.current;
      if (current && (current.pending || (current.ok && !fresh))) return current.promise;
      const entry = { pending: true, ok: false, promise: null };
      prefsHydrationRef.current = entry;
      entry.promise = Promise.resolve()
        .then(() => loadDictationPrefs())
        .then((loaded) => {
          // A loader that predates the boolean resolves undefined on success.
          entry.ok = loaded !== false;
        })
        .catch((err) => {
          // The store keeps its cross-platform seeds when the backend is not
          // ready. Readiness must still resolve so the native hotkey can work.
          console.warn('dictation prefs hydration failed:', err);
        })
        .then(() => {
          entry.pending = false;
          // Zustand updates before loadDictationPrefs resolves, but React's
          // selector effects may render later. Synchronise the long-lived
          // native listener refs now so its first event cannot use seed prefs.
          const prefs = useAppStore.getState();
          if (typeof prefs.dictationEnabled === 'boolean') {
            enabledRef.current = prefs.dictationEnabled;
          }
          if (prefs.dictationMode === 'toggle' || prefs.dictationMode === 'hold') {
            modeRef.current = prefs.dictationMode;
          }
        });
      return entry.promise;
    },
    [loadDictationPrefs],
  );
  // `state` follows the same rule, and for a sharper reason than the prefs do.
  // The tray listener used to depend on [state], so every single state change
  // tore the Tauri listener down and re-attached it through an `await import()`
  // + `await listen()` — a gap with NOTHING listening. A shortcut press that
  // landed in that gap was lost, and because the Rust side shows the widget
  // window BEFORE it emits `tray-dictate` (lib.rs), a lost press left the
  // window visible with the pill stuck in `idle` — which renders null, so all
  // the user saw was an empty square that Esc could not clear (the widget
  // deliberately never takes focus on macOS/Windows, #287/#982). Reading state
  // through a ref lets the listener attach exactly once, for the lifetime of
  // the component, so there is no gap to lose a press in.
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);
  // Same reason: the trigger callbacks are defined further down and change
  // identity, but the listener must not re-subscribe to follow them.
  const startRecordingRef = useRef(null);
  const stopRecordingRef = useRef(null);
  // Hold mode can be released while microphone permission or getUserMedia is
  // still pending. Preserve that release so the completed start cannot leave
  // an orphaned recording behind.
  const holdStartRef = useRef(null);
  const holdStartSequenceRef = useRef(0);
  // Some native accelerator stacks can deliver duplicate events. Collapse a
  // near-simultaneous pair without slowing intentional toggle-mode presses.
  const nativeEventAtRef = useRef({ start: 0, stop: 0 });

  // Sherpa live-streaming session refs. `sherpaModeRef` flips on at start when a
  // sherpa model is selected; `committedRef` accumulates per-utterance finals so
  // the pill can show the running transcript and the EOF summary can reconcile.
  const sherpaModeRef = useRef(false);
  const committedRef = useRef([]);
  // Live-typing state. `typedRef` is the exact text we have typed into the
  // focused field for the CURRENT in-flight utterance (committed utterances are
  // left alone — we never backspace across an utterance boundary). It resets to
  // '' each time an utterance is committed. `liveTypingRef` is seeded from the
  // LS_LIVE_TYPING pref at session start (default OFF — commit-only insert, no
  // visible backspace storms) and latches off if a simulate_type call fails.
  // A failure after possible emission is terminal; a zero-emission preflight
  // safely downgrades the rest of the session to committed delivery.
  const typedRef = useRef('');
  const liveTypingRef = useRef(false);
  // A native type command may emit only part of its delta before returning Err.
  // Once that happens, pasting the whole final could duplicate unknown text.
  const typingFailedRef = useRef(false);
  // Set after an utterance commits: the next utterance's first typed delta is
  // prefixed with a single separating space (so we don't trail a space after the
  // final utterance, and words across utterances don't run together).
  const pendingSepRef = useRef(false);
  // Serialise simulate_type calls: partials can arrive faster than the OS input
  // queue drains; chaining on this promise keeps backspaces/types strictly
  // ordered so a late delta can't interleave and corrupt the field.
  const typeChainRef = useRef(Promise.resolve());
  // Serialise per-utterance paste deliveries the same way, so finalise can
  // AWAIT them — the offline-model socket close races the last paste, and the
  // pill must not claim "Pasted" while an invoke is still in flight.
  const pasteChainRef = useRef(Promise.resolve());
  // First delivery failure of a live session (per-utterance paste). Checked at
  // finalise so the pill reports the truth instead of a green "Pasted".
  const segmentErrorRef = useRef(null);
  // Rust captures the target app before emitting the start event. Every native
  // delivery in this recording carries that same lease id until finish.
  const outputSessionIdRef = useRef(null);
  const captureGenerationRef = useRef(0);
  const deliveryKindRef = useRef(null);
  // A native `copied` outcome latches Rust into clipboard-only mode. A WebView
  // fallback does not, so its final summary must also use a non-inserting copy
  // path rather than retrying native paste and risking duplicate insertion.
  const deliveryCopySourceRef = useRef(null);
  const startInFlightRef = useRef(false);
  const finishInFlightRef = useRef(null);
  const nativeActivationChainRef = useRef(Promise.resolve());
  const nativeStartSequenceRef = useRef(0);
  // A candidate accepted while the previous microphone graph is still
  // starting normally adopts that graph. If the graph instead terminates
  // before startup unwinds, replay this already-activated candidate once.
  const pendingNativeStartRef = useRef(null);

  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const recordingFormatRef = useRef({ mimeType: 'audio/webm', extension: 'webm' });
  const streamRef = useRef(null);
  const timerRef = useRef(null);
  const wsRef = useRef(null);
  const wsPendingRef = useRef([]);
  const wsHadFinalRef = useRef(false);
  const fallbackTimerRef = useRef(null);
  const dismissTimerRef = useRef(null);
  const startTimeRef = useRef(0);
  // Waveform ring buffer (pure module) — fed by the worklet frame callbacks.
  const waveRef = useRef(null);
  if (!waveRef.current) waveRef.current = createWaveform();
  // The Accessibility setup pill is shown at most once per widget lifetime.
  const a11ySetupSeenRef = useRef(false);
  // Opt-in dictate-over-playback AEC (parity Action 8). When on, we capture
  // raw PCM via an AudioWorklet and tag mic/far-end frames instead of using
  // MediaRecorder. All AEC state lives in refs so the default path is inert.
  const aecModeRef = useRef(false);
  const pcmModeRef = useRef(false);
  const aecStopRef = useRef(null); // async teardown of the mic worklet graph
  const farEndUnsubRef = useRef(null); // unsubscribe from the far-end bus

  const finishOutputSession = useCallback((requestedId = null) => {
    const sessionId = requestedId || outputSessionIdRef.current;
    if (!sessionId || !inTauri()) return Promise.resolve(true);
    const previous = finishInFlightRef.current;
    if (previous?.sessionId === sessionId) return previous.promise;

    const operation = { sessionId, promise: null };
    operation.promise = (async () => {
      if (previous) await previous.promise;
      let released = false;
      try {
        await tauriInvoke('finish_dictation_output_session', { sessionId });
        released = true;
      } catch (err) {
        console.warn('finish dictation output session failed:', err);
      } finally {
        if (released && outputSessionIdRef.current === sessionId) {
          outputSessionIdRef.current = null;
        }
        if (finishInFlightRef.current === operation) finishInFlightRef.current = null;
      }
      return released;
    })();
    finishInFlightRef.current = operation;
    return operation.promise;
  }, []);

  const teardownAec = useCallback(async () => {
    try {
      farEndUnsubRef.current?.();
    } catch (err) {
      console.warn('far-end unsubscribe failed:', err);
    }
    farEndUnsubRef.current = null;
    const stop = aecStopRef.current;
    aecStopRef.current = null;
    try {
      await stop?.();
    } catch (err) {
      console.warn('mic worklet teardown failed:', err);
    }
    aecModeRef.current = false;
    pcmModeRef.current = false;
  }, []);

  // Browser buttons and focused-window shortcuts use the same request event;
  // the recorder remains owned by this single component.
  const browserRequestRef = useRef(null);
  browserRequestRef.current = (action) => {
    if (!enabledRef.current) return;
    const current = stateRef.current;
    if (action === 'stop') {
      if (current === 'recording') stopRecordingRef.current?.();
      else if (holdStartRef.current === 'starting') holdStartRef.current = 'released';
      return;
    }
    if (current === 'setup') {
      if (modeRef.current === 'hold') holdStartRef.current = 'starting';
      checkAccessibility().then((ok) => {
        if (ok) startRecordingRef.current?.(modeRef.current === 'hold');
        else holdStartRef.current = null;
      });
      return;
    }
    const idle = current === 'idle' || current === 'done' || current === 'error';
    if (action === 'toggle') {
      if (idle) startRecordingRef.current?.();
      else if (current === 'recording') stopRecordingRef.current?.();
    } else if (idle) {
      startRecordingRef.current?.(modeRef.current === 'hold');
    }
  };

  useEffect(() => {
    if (inTauri()) return;
    const onRequest = (event) => browserRequestRef.current?.(event.detail?.action || 'start');
    window.addEventListener(BROWSER_DICTATION_REQUEST, onRequest);
    return () => window.removeEventListener(BROWSER_DICTATION_REQUEST, onRequest);
  }, []);

  // Hydrate dictation prefs (enabled / mode / model) from the backend once. The
  // widget runs in its own Tauri webview (a separate JS context from the main
  // window), so it loads the prefs itself rather than relying on the Settings
  // window having loaded them.
  useEffect(() => {
    void ensureDictationPrefsHydrated();
  }, [ensureDictationPrefsHydrated]);

  // First-run truthfulness: without the macOS Accessibility grant native text
  // insertion is unavailable, though the completed transcript can still fall
  // back to the clipboard. Probe up front so the setup pill exposes the grant.
  // (Resolves true on Windows/Linux and outside Tauri.)
  useEffect(() => {
    let stale = false;
    (async () => {
      const ok = await checkAccessibility();
      if (!stale && !ok && !a11ySetupSeenRef.current) {
        a11ySetupSeenRef.current = true;
        setState((s) => (s === 'idle' ? 'setup' : s));
      }
    })();
    return () => {
      stale = true;
    };
  }, []);

  useEffect(() => {
    if (state !== 'setup' || !inTauri()) return undefined;
    let cancelled = false;
    let timerId;

    // Wall-clock start of this setup episode, so the budget covers the whole
    // time the prompt has been up rather than one poll interval.
    const shownAt = Date.now();
    let hiddenForBudget = false;
    const reconcileAccessibility = async () => {
      const ok = await checkAccessibility();
      if (cancelled || stateRef.current !== 'setup') return;
      if (ok) {
        stateRef.current = 'idle';
        setState('idle');
        await hideWidgetWindow();
        return;
      }
      if (!hiddenForBudget && Date.now() - shownAt >= A11Y_SETUP_VISIBLE_MS) {
        // Stop covering the screen, but stay in `setup` so the label is right
        // if something shows the window again, and keep polling below.
        hiddenForBudget = true;
        await hideWidgetWindow();
      }
      timerId = setTimeout(() => {
        void reconcileAccessibility();
      }, A11Y_SETUP_RECHECK_MS);
    };

    timerId = setTimeout(() => {
      void reconcileAccessibility();
    }, A11Y_SETUP_RECHECK_MS);
    return () => {
      cancelled = true;
      clearTimeout(timerId);
    };
  }, [state]);

  // ── Tray hotkey: tray-dictate (start) + tray-dictate-stop (release) ──
  // Toggle mode: tray-dictate flips start↔stop, tray-dictate-stop is ignored
  //   (Tauri only emits tray-dictate-stop on key *release* in hold registration;
  //   in toggle registration the backend emits tray-dictate on each press).
  // Hold mode: tray-dictate starts, tray-dictate-stop stops.
  // Both branches are gated on `enabled` so a disabled toggle makes the hotkey
  // inert. Behaviour is identical on macOS / Windows / Linux.
  useEffect(() => {
    if (!inTauri()) return; // browser webui — the keyboard fallback below runs
    let unlistenStart, unlistenStop;
    let registrationId;
    let endedRegistrationId;
    let cancelled = false;
    const teardownRegistration = async () => {
      const stopStart = unlistenStart;
      const stopStop = unlistenStop;
      unlistenStart = undefined;
      unlistenStop = undefined;
      try {
        stopStart?.();
      } catch (err) {
        console.warn('tray-dictate unlisten failed:', err);
      }
      try {
        stopStop?.();
      } catch (err) {
        console.warn('tray-dictate-stop unlisten failed:', err);
      }
      if (registrationId && endedRegistrationId !== registrationId) {
        endedRegistrationId = registrationId;
        await tauriInvoke('end_dictation_capture_registration', { registrationId });
      }
    };
    const acknowledgeDelivery = (event) => {
      const deliveryId = event?.payload?.deliveryId;
      const eventRegistrationId = event?.payload?.registrationId;
      if (eventRegistrationId != null && eventRegistrationId !== registrationId) return false;
      if (deliveryId != null) {
        return tauriInvoke('acknowledge_dictation_capture_delivery', {
          registrationId,
          deliveryId,
        })
          .catch((err) => {
            console.warn('dictation delivery acknowledgement failed:', err);
            return false;
          })
          .then((acknowledged) => acknowledged !== false);
      }
      return true;
    };
    const completeDelivery = async (event, error = null) => {
      const deliveryId = event?.payload?.deliveryId;
      if (deliveryId == null) return;
      try {
        await tauriInvoke('complete_dictation_capture_delivery', {
          registrationId,
          deliveryId,
          error,
        });
      } catch (err) {
        console.warn('dictation delivery completion failed:', err);
      }
    };
    (async () => {
      try {
        registrationId = await tauriInvoke('begin_dictation_capture_registration');
        if (cancelled) {
          await teardownRegistration();
          return;
        }
        const { listen } = await import('@tauri-apps/api/event');
        unlistenStart = await listen('tray-dictate', async (event) => {
          let acknowledgement = acknowledgeDelivery(event);
          if (acknowledgement === false) return;
          if (acknowledgement !== true) acknowledgement = await acknowledgement;
          if (!acknowledgement) return;
          const now = Date.now();
          if (now - nativeEventAtRef.current.start < 150) {
            await completeDelivery(event, 'Duplicate dictation start ignored');
            return;
          }
          nativeEventAtRef.current.start = now;
          const sessionId = event?.payload?.sessionId;
          if (!sessionId) {
            hideWidgetWindow();
            await completeDelivery(event, 'Dictation output session is missing');
            return;
          }
          // Fresh: the model may have been changed from the main window
          // since this store last loaded (see ensureDictationPrefsHydrated).
          await ensureDictationPrefsHydrated({ fresh: true });
          if (!enabledRef.current) {
            // The hotkey is inert, but Rust has already shown the window.
            // Put it back rather than leaving an empty capsule on screen.
            hideWidgetWindow();
            await finishOutputSession(sessionId);
            await completeDelivery(event, 'Dictation is disabled');
            return;
          }
          const sequence = ++nativeStartSequenceRef.current;
          const s = stateRef.current;
          const startupWasInFlight = startInFlightRef.current;
          const restartable = s === 'idle' || s === 'done' || s === 'error' || s === 'setup';
          if (!restartable && !startupWasInFlight) {
            if (modeRef.current === 'toggle' && s === 'recording') {
              stopRecordingRef.current?.();
            }
            try {
              await tauriInvoke('reject_dictation_output_session', { sessionId });
            } catch (err) {
              console.warn('reject dictation output session failed:', err);
            }
            await completeDelivery(event, 'Dictation is already active');
            return;
          }
          const trackHold = modeRef.current === 'hold';
          if (trackHold) {
            holdStartSequenceRef.current = sequence;
            holdStartRef.current = 'starting';
          }
          const clearPendingHold = () => {
            if (holdStartSequenceRef.current !== sequence) return;
            holdStartRef.current = null;
            holdStartSequenceRef.current = 0;
          };

          const activation = nativeActivationChainRef.current.then(() =>
            tauriInvoke('activate_dictation_output_session', { sessionId }),
          );
          nativeActivationChainRef.current = activation.catch(() => {});
          try {
            await activation;
          } catch (err) {
            console.warn('activate dictation output session failed:', err);
            clearPendingHold();
            await finishOutputSession(sessionId);
            hideWidgetWindow();
            await completeDelivery(event, `Could not activate dictation output: ${err}`);
            return;
          }
          if (cancelled || sequence !== nativeStartSequenceRef.current || !enabledRef.current) {
            clearPendingHold();
            await finishOutputSession(sessionId);
            await completeDelivery(event, 'Dictation start was cancelled');
            return;
          }
          if (startupWasInFlight) {
            const current = stateRef.current;
            if (startInFlightRef.current) {
              outputSessionIdRef.current = sessionId;
              pendingNativeStartRef.current = { sessionId, trackHold, sequence };
              await completeDelivery(event, 'Another dictation start is already in progress');
            } else if (current === 'recording' || current === 'transcribing') {
              outputSessionIdRef.current = sessionId;
              await completeDelivery(event);
            } else if (
              current === 'idle' ||
              current === 'done' ||
              current === 'error' ||
              current === 'setup'
            ) {
              outputSessionIdRef.current = sessionId;
              void Promise.resolve(startRecordingRef.current?.(trackHold, sessionId)).then(
                (accepted) =>
                  completeDelivery(event, accepted ? null : 'Dictation could not start'),
              );
            } else {
              clearPendingHold();
              await finishOutputSession(sessionId);
              await completeDelivery(event, 'Dictation could not accept this start');
            }
            return;
          }
          if (s === 'setup') {
            // Re-probe on each press — the user may have just granted access
            // in System Settings. A missing grant no longer blocks capture:
            // native delivery can truthfully fall back to clipboard-only.
            outputSessionIdRef.current = sessionId;
            void checkAccessibility().then(async () => {
              if (outputSessionIdRef.current !== sessionId) {
                await completeDelivery(event, 'Dictation output session changed before startup');
                return;
              }
              const accepted = await startRecordingRef.current?.(
                modeRef.current === 'hold',
                sessionId,
              );
              await completeDelivery(event, accepted ? null : 'Dictation could not start');
            });
            return;
          }
          const idle = s === 'idle' || s === 'done' || s === 'error';
          if (modeRef.current === 'toggle') {
            // Press once to start, again to stop.
            if (idle) {
              void Promise.resolve(startRecordingRef.current?.(false, sessionId)).then((accepted) =>
                completeDelivery(event, accepted ? null : 'Dictation could not start'),
              );
              return;
            } else if (s === 'recording') {
              stopRecordingRef.current?.();
              await completeDelivery(event);
              return;
            }
          } else if (idle) {
            // Hold mode: keydown → start.
            void Promise.resolve(startRecordingRef.current?.(true, sessionId)).then((accepted) =>
              completeDelivery(event, accepted ? null : 'Dictation could not start'),
            );
            return;
          }
          await completeDelivery(event, 'Dictation could not start');
        });
        unlistenStop = await listen('tray-dictate-stop', async (event) => {
          let acknowledgement = acknowledgeDelivery(event);
          if (acknowledgement === false) return;
          if (acknowledgement !== true) acknowledgement = await acknowledgement;
          if (!acknowledgement) return;
          const now = Date.now();
          if (now - nativeEventAtRef.current.stop < 150) {
            await completeDelivery(event, 'Duplicate dictation stop ignored');
            return;
          }
          nativeEventAtRef.current.stop = now;
          await ensureDictationPrefsHydrated();
          // Only hold mode acts on release; toggle ignores it.
          let accepted = false;
          if (modeRef.current === 'hold' && stateRef.current === 'recording') {
            stopRecordingRef.current?.();
            accepted = true;
          } else if (modeRef.current === 'hold' && holdStartRef.current === 'starting') {
            holdStartRef.current = 'released';
            accepted = true;
          }
          await completeDelivery(
            event,
            accepted ? null : 'Dictation is not recording in hold mode',
          );
        });
        await ensureDictationPrefsHydrated();
        if (cancelled) {
          await teardownRegistration();
          return;
        }
        await tauriInvoke('mark_dictation_capture_ready', { registrationId });
        // Unmounted while the dynamic import was in flight — drop the
        // subscriptions we just created rather than leaking them.
        if (cancelled) {
          await teardownRegistration();
        }
      } catch (err) {
        await teardownRegistration().catch(() => {});
        // Hotkey wiring failed inside Tauri — dictation still works via the
        // in-page shortcut, but say so in the console for bug reports.
        console.warn('tray-dictate listen failed:', err);
      }
    })();
    return () => {
      cancelled = true;
      nativeStartSequenceRef.current += 1;
      void teardownRegistration();
    };
    // Attach ONCE — see stateRef above. Adding a dependency here reintroduces
    // the dropped-press window that stranded the widget.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keyboard fallback (web UI / Docker — no global tray hotkey). Mirrors the
  // tray semantics so the DEFAULT dictation behaviour is identical with or
  // without Tauri: Toggle = keydown flips start↔stop; Hold = keydown starts,
  // keyup stops. The Ctrl/Cmd+Shift+Space combo matches the documented default
  // shortcut; the desktop app's user-rebindable accelerator is a Tauri concern.
  useEffect(() => {
    if (inTauri()) return;
    const isCombo = (e) => (e.metaKey || e.ctrlKey) && e.shiftKey && e.code === 'Space';
    const onKeyDown = (e) => {
      if (!isCombo(e)) return;
      e.preventDefault();
      browserRequestRef.current?.(modeRef.current === 'toggle' ? 'toggle' : 'start');
    };
    const onKeyUp = (e) => {
      // Hold mode stops as soon as Space (or a modifier) is released.
      if (modeRef.current !== 'hold') return;
      if (e.code !== 'Space' && e.key !== 'Meta' && e.key !== 'Control' && e.key !== 'Shift')
        return;
      browserRequestRef.current?.('stop');
    };
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
    };
  }, [state]);

  // Timer while recording
  useEffect(() => {
    if (state === 'recording' && !paused) {
      let previous = Date.now();
      timerRef.current = setInterval(() => {
        const now = Date.now();
        setDuration((elapsed) => elapsed + now - previous);
        previous = now;
      }, 100);
      return () => clearInterval(timerRef.current);
    }
    clearInterval(timerRef.current);
  }, [state, paused]);

  // Waveform poll: 50 ms ≈ 2–3 worklet frames, so bars visibly move well
  // within ~100 ms of mic start. Only runs while the worklet is feeding us.
  useEffect(() => {
    if (state !== 'recording' || !waveOn) return;
    const id = setInterval(() => setBars(waveRef.current.getBars(WAVE_BARS)), 50);
    return () => clearInterval(id);
  }, [state, waveOn]);

  // Reset the pill to hidden-idle and hide the widget window. Every dismissal
  // (X button, Esc, auto-dismiss) funnels through here.
  const dismiss = useCallback(async () => {
    if (dismissTimerRef.current) {
      clearTimeout(dismissTimerRef.current);
      dismissTimerRef.current = null;
    }
    if (aecModeRef.current || sherpaModeRef.current || pcmModeRef.current) teardownAec();
    pausedRef.current = false;
    setPaused(false);
    setState('idle');
    setTranscript('');
    setPartialText('');
    setDuration(0);
    setModelStatus(null);
    setErrorInfo(null);
    setDoneKind(null);
    await finishOutputSession();
    await hideWidgetWindow();
    if (onDismiss) onDismiss();
  }, [finishOutputSession, teardownAec, onDismiss]);

  useEffect(
    () => () => {
      void finishOutputSession();
    },
    [finishOutputSession],
  );

  // Auto-dismiss after a beat, tracked in a ref so Esc or a fresh session can
  // cancel it (a stale timer must never hide a newly-started recording).
  const scheduleDismiss = useCallback(
    (delay) => {
      if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
      dismissTimerRef.current = setTimeout(() => {
        dismissTimerRef.current = null;
        dismiss();
      }, delay);
    },
    [dismiss],
  );

  // Stop every capture input (recorder / worklet / tracks) without touching
  // the pill state — shared by stop, cancel and the WS error path.
  const stopCaptureGraph = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    if (aecModeRef.current || sherpaModeRef.current || pcmModeRef.current) {
      teardownAec();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }, [teardownAec]);

  // The widget can unmount while startup, capture, transcription or a delayed
  // dismissal is still in flight (Tauri can replace the widget webview). Make
  // every continuation stale, detach its transport callbacks, and release every
  // browser-owned capture resource without updating React during teardown.
  useEffect(
    () => () => {
      captureGenerationRef.current += 1;
      pendingNativeStartRef.current = null;
      startRecordingRef.current = null;
      stopRecordingRef.current = null;
      wsHadFinalRef.current = true;
      if (dismissTimerRef.current) {
        clearTimeout(dismissTimerRef.current);
        dismissTimerRef.current = null;
      }
      if (fallbackTimerRef.current) {
        clearTimeout(fallbackTimerRef.current);
        fallbackTimerRef.current = null;
      }
      const ws = wsRef.current;
      wsRef.current = null;
      wsPendingRef.current = [];
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        try {
          ws.close();
        } catch (err) {
          console.warn('capture socket teardown failed:', err);
        }
      }
      const recorder = mediaRecorderRef.current;
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onstop = null;
      }
      stopCaptureGraph();
      mediaRecorderRef.current = null;
      chunksRef.current = [];
    },
    [stopCaptureGraph],
  );

  // Esc = abort. Stops capture, discards the audio and any in-flight result
  // (nothing is pasted), closes the socket and hides the pill.
  const cancelSession = useCallback(() => {
    captureGenerationRef.current += 1;
    pendingNativeStartRef.current = null;
    wsHadFinalRef.current = true; // any late final/fallback result is discarded
    if (fallbackTimerRef.current) {
      clearTimeout(fallbackTimerRef.current);
      fallbackTimerRef.current = null;
    }
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) ws.close();
    stopCaptureGraph();
    committedRef.current = [];
    typingFailedRef.current = false;
    typeChainRef.current = Promise.resolve();
    pasteChainRef.current = Promise.resolve();
    setTrayRecording(false);
    dismiss();
  }, [stopCaptureGraph, dismiss]);

  // Esc cancels in EVERY state (window-level, so it works wherever focus sits
  // inside the widget): recording/transcribing → abort + discard; done/error/
  // setup → dismiss.
  useEffect(() => {
    if (state === 'idle') return;
    const onEsc = (e) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      if (state === 'recording' || state === 'transcribing') cancelSession();
      else dismiss();
    };
    window.addEventListener('keydown', onEsc);
    return () => window.removeEventListener('keydown', onEsc);
  }, [state, cancelSession, dismiss]);

  // Safety net: an error pill with nothing to rescue must never strand on the
  // user's screen. Delivery failures deliberately stay up — they hold the
  // transcript the user may still need to copy — but a mic / model-missing /
  // server / connection failure has no text to preserve, and those paths used
  // to leave the widget visible forever (reported as "the dictation bubble is
  // permanently sticking when it's not used"). One effect covers every error
  // path, including any added later, so no single call site can reintroduce it.
  useEffect(() => {
    if (state !== 'error' || transcript) return;
    const t = setTimeout(() => {
      if (!startInFlightRef.current) dismiss();
    }, ERROR_AUTO_DISMISS_MS);
    return () => clearTimeout(t);
  }, [state, transcript, dismiss]);

  // Apply transcription result → deliver (paste/copy) → show the TRUE outcome
  // → auto-dismiss on success. A failed delivery is an error state (with the
  // Accessibility action when that's the fix), never a fake "Pasted".
  const applyResult = useCallback(
    async (
      data,
      sessionId = outputSessionIdRef.current,
      generation = captureGenerationRef.current,
    ) => {
      const isCurrent = () =>
        generation === captureGenerationRef.current &&
        (!sessionId || outputSessionIdRef.current === sessionId);
      if (!isCurrent()) {
        await finishOutputSession(sessionId);
        return;
      }
      // Wave 2.1: the backend may attach an LLM-refined version of the final
      // text (filler words removed, self-corrections applied). Paste/show the
      // refined text when present; the raw text is kept in history alongside.
      const finalText = data.refined_text || data.text || '';
      setTranscript(finalText);
      setLastEngine(data.engine || '');
      setLastTime(data.transcription_time_s || 0);
      setModelStatus(null);

      if (data.text) {
        addTranscription(data);
      }

      if (!finalText) {
        // No speech — brief notice, then auto-dismiss.
        await finishOutputSession(sessionId);
        if (captureGenerationRef.current !== generation) return;
        setDoneKind(null);
        setState('done');
        scheduleDismiss(2500);
        return;
      }

      const res = await deliverText(finalText, sessionId);
      if (!isCurrent()) {
        await finishOutputSession(sessionId);
        return;
      }
      await finishOutputSession(sessionId);
      if (captureGenerationRef.current !== generation) return;
      if (res.ok) {
        setDoneKind(res.kind);
        setState('done');
        scheduleDismiss(1500);
      } else {
        // The transcript did NOT land. Keep the pill up until the user acts;
        // native delivery owns clipboard fallback and reports that as success.
        setErrorInfo(res.error);
        setState('error');
      }
    },
    [finishOutputSession, scheduleDismiss],
  );

  const queueSegmentPaste = useCallback((text) => {
    const sessionId = outputSessionIdRef.current;
    const generation = captureGenerationRef.current;
    const typeBarrier = typeChainRef.current;
    const isCurrent = () =>
      generation === captureGenerationRef.current &&
      (!sessionId || outputSessionIdRef.current === sessionId);
    const run = async () => {
      // A summary can race the last live-type invoke. Wait for that command's
      // outcome before deciding whether paste is safe.
      await typeBarrier;
      if (!isCurrent()) return;
      if (typingFailedRef.current) return;
      // Once native delivery falls back to clipboard-only, wait for the
      // authoritative summary instead of repeatedly replacing it with pieces.
      if (deliveryKindRef.current === 'copied') return;
      const result = await pasteSegment(text, sessionId);
      if (!isCurrent()) return;
      if (result.ok) {
        deliveryKindRef.current = aggregateDeliveryKind(deliveryKindRef.current, result.kind);
        if (result.copySource) deliveryCopySourceRef.current = result.copySource;
      } else if (!segmentErrorRef.current) {
        segmentErrorRef.current = result.error;
      }
    };
    const previous = pasteChainRef.current;
    pasteChainRef.current = previous.then(run, run);
    return pasteChainRef.current;
  }, []);

  // Finalise a sherpa LIVE-streaming session. The per-utterance finals were
  // already delivered into the focused field as the user paused, so this does
  // NOT re-paste — it shows the authoritative full transcript in the pill,
  // reports any segment that failed to land, and auto-dismisses on success.
  // The EOF-summary `final` (or an early socket close) drives this.
  const finalizeSession = useCallback(
    async (
      data,
      sessionId = outputSessionIdRef.current,
      generation = captureGenerationRef.current,
    ) => {
      const isCurrent = () =>
        generation === captureGenerationRef.current &&
        (!sessionId || outputSessionIdRef.current === sessionId);
      // Wait for in-flight per-utterance deliveries first — the outcome the
      // pill reports must be the settled one, not a hopeful guess.
      // A pending type preflight can downgrade an utterance and enqueue its
      // committed delivery as the type chain settles. Read pasteChain only
      // after that point so finalisation cannot race the newly queued paste.
      await typeChainRef.current;
      if (!isCurrent()) {
        await finishOutputSession(sessionId);
        return;
      }
      await pasteChainRef.current;
      if (!isCurrent()) {
        await finishOutputSession(sessionId);
        return;
      }
      const fullText = data.refined_text || data.text || '';
      if (typingFailedRef.current && fullText) {
        const rescue = await copySessionText(fullText, sessionId);
        if (!isCurrent()) {
          await finishOutputSession(sessionId);
          return;
        }
        if (rescue.ok) {
          deliveryKindRef.current = aggregateDeliveryKind(deliveryKindRef.current, rescue.kind);
          if (rescue.copySource) deliveryCopySourceRef.current = rescue.copySource;
          segmentErrorRef.current = null;
        } else {
          segmentErrorRef.current = rescue.error;
        }
      } else if (deliveryKindRef.current === 'copied' && fullText) {
        // Only a native `copied` outcome latches Rust clipboard-only. If native
        // delivery failed and WebKit supplied the clipboard fallback, retrying
        // simulate_paste could recover and duplicate already inserted segments.
        const refresh =
          deliveryCopySourceRef.current === 'webview'
            ? await copyText(fullText)
                .then(() => ({ ok: true, kind: 'copied', copySource: 'webview' }))
                .catch((err) => ({
                  ok: false,
                  error: { kind: 'clipboard', message: String(err?.message || err) },
                }))
            : await deliverText(fullText, sessionId);
        if (!isCurrent()) {
          await finishOutputSession(sessionId);
          return;
        }
        if (refresh.ok) {
          deliveryKindRef.current = aggregateDeliveryKind(deliveryKindRef.current, refresh.kind);
          if (refresh.copySource) deliveryCopySourceRef.current = refresh.copySource;
        } else if (!segmentErrorRef.current) {
          segmentErrorRef.current = refresh.error;
        }
      }
      await finishOutputSession(sessionId);
      if (captureGenerationRef.current !== generation) return;
      setTranscript(fullText);
      setLastEngine(data.engine || 'sherpa-onnx-asr');
      setLastTime(data.transcription_time_s || 0);
      setModelStatus(null);
      // NB: history was already recorded per-utterance as each `final` was
      // delivered live (see the message handler), so finalisation does NOT
      // re-record — that would duplicate the session.
      setPartialText('');
      committedRef.current = [];
      typingFailedRef.current = false;
      const deliveryKind = deliveryKindRef.current;
      deliveryKindRef.current = null;
      deliveryCopySourceRef.current = null;
      if (segmentErrorRef.current) {
        // At least one utterance never reached the target app — the truthful
        // outcome is an error (with the a11y action when relevant).
        setErrorInfo(segmentErrorRef.current);
        setState('error');
        return;
      }
      setDoneKind(fullText ? deliveryKind : null);
      setState('done');
      scheduleDismiss(fullText ? 1500 : 2500);
    },
    [finishOutputSession, scheduleDismiss],
  );

  // Type the recognizer's latest revision of the in-flight utterance into the
  // focused field, reconciling against what we typed before via a prefix diff.
  // Serialised on `typeChainRef` so concurrent partials can't interleave. If the
  // delta typing fails, latch live typing off. Zero-emission preflight failures
  // downgrade to committed delivery; a possibly partial emission is terminal.
  const liveType = useCallback((nextText) => {
    if (!liveTypingRef.current) return typeChainRef.current;
    const generation = captureGenerationRef.current;
    const sessionId = outputSessionIdRef.current;
    const isCurrent = () =>
      generation === captureGenerationRef.current &&
      (!sessionId || outputSessionIdRef.current === sessionId);
    const run = async () => {
      if (!isCurrent() || !liveTypingRef.current) return;
      // Prefix the first delta of a new (non-first) utterance with a separator,
      // tracked inside typedRef so the diff stays self-consistent.
      let target = nextText || '';
      if (pendingSepRef.current && target !== '') {
        target = ' ' + target;
        pendingSepRef.current = false;
      }
      const delta = computeTypeDelta(typedRef.current, target);
      if (delta.noop) return;
      const result = await typeDelta(delta, sessionId);
      if (!isCurrent()) return;
      if (result.ok) {
        typedRef.current = target;
        deliveryKindRef.current = aggregateDeliveryKind(deliveryKindRef.current, result.kind);
      } else {
        // Stop live typing for the rest of the session. A native preflight or
        // Accessibility failure emits nothing, so committed paste/copy remains
        // safe only when no earlier delta for this utterance landed. Synthesis
        // failures and an already-inserted prefix both make a whole retry unsafe.
        liveTypingRef.current = false;
        if (result.mayHaveEmitted || typedRef.current !== '') {
          typingFailedRef.current = true;
          if (!segmentErrorRef.current) segmentErrorRef.current = result.error;
        }
      }
    };
    const previous = typeChainRef.current;
    typeChainRef.current = previous.then(run, run);
    return typeChainRef.current;
  }, []);

  const startRecordingImpl = useCallback(
    async (generation, startupSessionId = outputSessionIdRef.current) => {
      const isCurrent = () => generation === captureGenerationRef.current;
      // A newer native session can adopt this microphone graph while setup is
      // still awaiting permissions/worklets. If the old attempt then fails,
      // release only its original lease; finishing the adopted lease would
      // make the replay below stale before it can start a fresh graph.
      const finishAttemptOutputSession = () => {
        const pending = pendingNativeStartRef.current;
        const replacementOwnsCurrent =
          pending?.sessionId === outputSessionIdRef.current &&
          startupSessionId &&
          startupSessionId !== pending.sessionId;
        return finishOutputSession(
          replacementOwnsCurrent ? startupSessionId : outputSessionIdRef.current,
        );
      };
      // Pre-flight: when the OS itself reports the mic grant as DENIED,
      // getUserMedia can only throw an opaque NotAllowedError — skip it and
      // show the guided path (per-OS hint + Open Settings deep-link) instead.
      // 'prompt'/'granted'/'unknown' proceed exactly as before (getUserMedia
      // raises the OS prompt; micError.js stays the reactive fallback), and
      // outside Tauri checkMicrophone() is always 'unknown' → unchanged.
      const microphoneState = await checkMicrophone();
      if (!isCurrent()) return;
      if (microphoneState === 'denied') {
        holdStartRef.current = null;
        showMicDeniedGuide(t);
        setTrayRecording(false);
        setErrorInfo({
          kind: 'mic',
          message: t(micHintKey(detectPlatform())),
          deniedByOs: true,
        });
        setState('error');
        await finishAttemptOutputSession();
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 },
        });
        if (!isCurrent()) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        streamRef.current = stream;
        chunksRef.current = [];
        recordingFormatRef.current = { mimeType: 'audio/webm', extension: 'webm' };
        wsPendingRef.current = [];
        wsHadFinalRef.current = false;
        committedRef.current = [];
        segmentErrorRef.current = null;
        deliveryKindRef.current = null;
        deliveryCopySourceRef.current = null;
        typedRef.current = '';
        typingFailedRef.current = false;
        // Live retract-retype is OPT-IN (visible backspace storms in the target
        // app unnerved users): default sessions insert only committed finals via
        // the paste path; the pref re-enables word-by-word typing.
        liveTypingRef.current = localStorage.getItem(LS_LIVE_TYPING) === '1';
        pendingSepRef.current = false;
        typeChainRef.current = Promise.resolve();
        pasteChainRef.current = Promise.resolve();
        waveRef.current.reset();
        if (fallbackTimerRef.current) {
          clearTimeout(fallbackTimerRef.current);
          fallbackTimerRef.current = null;
        }
        if (dismissTimerRef.current) {
          clearTimeout(dismissTimerRef.current);
          dismissTimerRef.current = null;
        }

        // Read prefs at start time (avoids stale closures). AEC is opt-in; the
        // sherpa live engine is selected when the persisted dictation model is a
        // sherpa-onnx model — that path streams raw int16 PCM and emits live
        // partials + a `final` per spoken utterance (committed on silence).
        const aecOn = useAppStore.getState().aecEnabled === true;
        const modelId = useAppStore.getState().dictationModelId;
        const sherpaOn = isSherpaModel(modelId);
        const supportedRecorder =
          aecOn || sherpaOn
            ? null
            : startSupportedMediaRecorder(stream, {
                onData: (e) => {
                  if (!isCurrent() || e.data.size === 0) return;
                  if (e.data.type) recordingFormatRef.current = audioFormatForMimeType(e.data.type);
                  chunksRef.current.push(e.data);
                  void e.data.arrayBuffer().then((buf) => {
                    if (!isCurrent()) return;
                    const ws = wsRef.current;
                    if (ws && ws.readyState === WebSocket.OPEN) ws.send(buf);
                    else wsPendingRef.current.push(buf);
                  });
                },
                onStop: () => {},
              });
        const pcmFallback = !aecOn && !sherpaOn && supportedRecorder === null;
        if (supportedRecorder) mediaRecorderRef.current = supportedRecorder.recorder;
        aecModeRef.current = aecOn;
        sherpaModeRef.current = sherpaOn;
        pcmModeRef.current = pcmFallback;
        // Raw-PCM transport is used whenever AEC or the sherpa live engine is on.
        const pcmMode = aecOn || sherpaOn || pcmFallback;

        // Open WebSocket BEFORE starting capture.
        try {
          // Scheme + host derive from the API base (window.location lies inside
          // the Tauri webview). A remote bearer session is converted to a fresh,
          // path-bound WebSocket ticket; neither the master nor session token is
          // ever placed in this URL.
          //   • sherpa → ?model=<id>&sr=16000  (raw int16 PCM, live partials)
          //   • AEC    → ?aec=1&sr=16000       (tagged raw PCM, NLMS canceller)
          //   • both   → ?model=<id>&aec=1&sr=16000
          //   • no recorder → ?pcm=1&sr=16000  (WebKitGTK fallback)
          //   • otherwise → /ws/transcribe     (negotiated media container)
          const params = [];
          if (sherpaOn) params.push(`model=${encodeURIComponent(modelId)}`);
          if (aecOn) params.push('aec=1');
          if (pcmFallback) params.push('pcm=1');
          if (pcmMode) params.push('sr=16000');
          const wsPath = params.length ? `/ws/transcribe?${params.join('&')}` : '/ws/transcribe';
          const endpoint = await authenticatedWsUrl(wsPath, { apiBase: API });
          if (!isCurrent()) {
            stopCaptureGraph();
            return;
          }
          const ws = new WebSocket(endpoint);
          ws.binaryType = 'arraybuffer';
          const failRawPcmSession = () => {
            if (
              wsHadFinalRef.current ||
              !(sherpaModeRef.current || aecModeRef.current || pcmModeRef.current)
            ) {
              return false;
            }
            wsHadFinalRef.current = true;
            stopCaptureGraph();
            setTrayRecording(false);
            setModelStatus(null);
            setErrorInfo({ kind: 'server', message: '' });
            setState('error');
            void finishAttemptOutputSession();
            return true;
          };
          ws.onopen = () => {
            if (!isCurrent() || wsRef.current !== ws) return;
            for (const buf of wsPendingRef.current) {
              try {
                ws.send(buf);
              } catch (err) {
                // Socket died mid-flush — onclose/onerror handles recovery.
                console.warn('ws flush failed:', err);
                break;
              }
            }
            wsPendingRef.current = [];
            if (pausedRef.current) ws.send('PAUSE');
          };
          ws.onmessage = async (evt) => {
            if (!isCurrent() || wsRef.current !== ws) return;
            let msg;
            try {
              msg = JSON.parse(evt.data);
            } catch (err) {
              console.warn('unparseable /ws/transcribe frame:', err);
              return;
            }
            if (msg.type === 'status') {
              // Model lifecycle truthfulness: while the backend fetches/loads
              // the ASR model it streams {stage:"downloading",progress} /
              // {stage:"loading"} / {stage:"ready"} so the pill can say what is
              // actually happening instead of a generic "Listening…".
              setModelStatus(
                msg.stage === 'ready'
                  ? null
                  : {
                      stage: msg.stage,
                      progress: typeof msg.progress === 'number' ? msg.progress : null,
                    },
              );
            } else if (msg.type === 'partial') {
              // Live interim text — show the running transcript so far plus the
              // in-flight partial, so the pill reads as continuous speech.
              const committed = committedRef.current.join(' ');
              const live = [committed, msg.text || ''].filter(Boolean).join(' ');
              setPartialText(live);
              // …and (opt-in) type the revised in-flight utterance into the
              // focused field word-by-word. The diff handles recognizer
              // self-corrections via backspaces; committed utterances are
              // untouched. Only sherpa live partials drive typing — the legacy
              // WebM path has no partials — and liveType no-ops unless the
              // LS_LIVE_TYPING pref opted in.
              if (sherpaModeRef.current) liveType(msg.text || '');
            } else if (msg.type === 'final') {
              if (sherpaModeRef.current) {
                if (msg.model_silent && !(msg.text || '').trim()) {
                  // Speech reached the selected model, but it produced no text.
                  // This is a broken-model result, not a successful quiet session.
                  wsHadFinalRef.current = true;
                  if (fallbackTimerRef.current) {
                    clearTimeout(fallbackTimerRef.current);
                    fallbackTimerRef.current = null;
                  }
                  stopCaptureGraph();
                  setTrayRecording(false);
                  setModelStatus(null);
                  setTranscript('');
                  setErrorInfo({ kind: 'transcription', message: '' });
                  setState('error');
                  await finishAttemptOutputSession();
                  ws.close();
                  return;
                }
                // Never infer the frame kind from text equality: two utterances
                // may be identical, while EOF may contain an uncommitted tail.
                const segText = msg.refined_text || msg.text || '';
                const cls = classifySherpaFinal(msg);
                if (cls === 'summary' || cls === 'terminator') {
                  if (cls === 'summary') {
                    const tail = sherpaSummaryTail(msg.text || '', committedRef.current);
                    if (tail) {
                      const deliveryText = committedRef.current.length
                        ? ` ${tail}`
                        : msg.refined_text || tail;
                      if (!typingFailedRef.current) queueSegmentPaste(deliveryText);
                      if (!committedRef.current.length && msg.text) addTranscription(msg);
                    }
                  }
                  wsHadFinalRef.current = true;
                  if (fallbackTimerRef.current) {
                    clearTimeout(fallbackTimerRef.current);
                    fallbackTimerRef.current = null;
                  }
                  finalizeSession(msg, outputSessionIdRef.current, generation);
                  ws.close();
                } else if (cls === 'utterance') {
                  // A per-utterance commit. Reconcile the focused field to the
                  // recognizer's AUTHORITATIVE final for this utterance (it can
                  // differ from the last partial — e.g. final punctuation / a
                  // late self-correction), then FREEZE it: reset typedRef so the
                  // next utterance's partials diff from empty. We never backspace
                  // across this boundary. In the default (live typing off) the
                  // committed final is pasted instead — never both (no
                  // double-insert) — and a failed paste is recorded so the
                  // session resolves truthfully.
                  const needsSeparator = committedRef.current.length > 0;
                  committedRef.current.push(segText);
                  setPartialText(committedRef.current.join(' '));
                  if (msg.text) addTranscription(msg);
                  if (liveTypingRef.current) {
                    const commitGeneration = captureGenerationRef.current;
                    const commitSessionId = outputSessionIdRef.current;
                    liveType(segText);
                    typeChainRef.current = typeChainRef.current.then(() => {
                      if (
                        commitGeneration !== captureGenerationRef.current ||
                        (commitSessionId && outputSessionIdRef.current !== commitSessionId)
                      ) {
                        return;
                      }
                      const commitAfterSafeDowngrade =
                        !liveTypingRef.current &&
                        !typingFailedRef.current &&
                        typedRef.current === '';
                      typedRef.current = '';
                      // Seed the next utterance's typed-state with a separating
                      // space (matching the ' '.join used by the pill/history) so
                      // its first delta types " word" — words never run together,
                      // and there is no trailing space after the LAST utterance.
                      pendingSepRef.current = true;
                      if (commitAfterSafeDowngrade) {
                        queueSegmentPaste(needsSeparator ? ` ${segText}` : segText);
                      }
                    });
                  } else if (typingFailedRef.current) {
                    // A failed native delta may have partially landed even when
                    // typedRef has no confirmed prefix. Never risk duplicating it.
                    typedRef.current = '';
                  } else {
                    queueSegmentPaste(needsSeparator ? ` ${segText}` : segText);
                  }
                }
              } else {
                // Legacy single-final path (Whisper/WebM) — unchanged.
                wsHadFinalRef.current = true;
                if (fallbackTimerRef.current) {
                  clearTimeout(fallbackTimerRef.current);
                  fallbackTimerRef.current = null;
                }
                applyResult(msg, outputSessionIdRef.current, generation);
                ws.close();
              }
            } else if (msg.type === 'error') {
              if (fallbackTimerRef.current) {
                clearTimeout(fallbackTimerRef.current);
                fallbackTimerRef.current = null;
              }
              ws.close();
              wsRef.current = null;
              if (asrMissingPayload(msg)) {
                // Typed preflight: no ASR model installed. The POST fallback
                // would hit the same 409, so don't re-send — render the
                // download CTA and resolve the pill into its error state.
                wsHadFinalRef.current = true;
                stopCaptureGraph();
                setTrayRecording(false);
                setModelStatus(null);
                const missing = asrMissingPayload(msg);
                // In the desktop app this window has no <Toaster>, so a toast
                // here rendered nowhere; the main window shows the install
                // action instead (dictationNotice, kind 'asr_missing'). The
                // browser build mounts this widget inside the main window,
                // where the local toast IS the only one.
                if (!inTauri()) toastAsrModelMissing(missing);
                setErrorInfo({ kind: 'asr_missing', message: t('asr_missing.message'), missing });
                setState('error');
                void finishAttemptOutputSession();
              } else if (sherpaModeRef.current || aecModeRef.current || pcmModeRef.current) {
                // Raw-PCM paths have no WebM blob to re-POST — surface the
                // backend's error instead of leaving the pill wedged in
                // "Transcribing…" forever.
                wsHadFinalRef.current = true;
                stopCaptureGraph();
                setTrayRecording(false);
                setModelStatus(null);
                setErrorInfo({ kind: msg.kind || 'server', message: msg.message || '' });
                setState('error');
                void finishAttemptOutputSession();
              } else if (!wsHadFinalRef.current) {
                sendForTranscription(outputSessionIdRef.current, generation);
              }
            }
          };
          ws.onerror = () => {
            if (!isCurrent() || wsRef.current !== ws) return;
            wsRef.current = null;
            failRawPcmSession();
          };
          ws.onclose = () => {
            // A terminal path can await native session release while a new
            // candidate is activated and starts its own socket. The old close
            // must never clear or finalise that newer recording.
            if (!isCurrent() || wsRef.current !== ws) return;
            wsRef.current = null;
            if (sherpaModeRef.current) {
              // Sherpa: nothing to POST (no WebM blob). If the socket dropped
              // before the EOF summary but we committed utterances live, close out
              // the session from what we have so the pill resolves.
              if (!wsHadFinalRef.current && committedRef.current.length) {
                wsHadFinalRef.current = true;
                finalizeSession(
                  { text: committedRef.current.join(' '), engine: 'sherpa-onnx-asr' },
                  outputSessionIdRef.current,
                  generation,
                );
              } else if (!wsHadFinalRef.current) {
                wsHadFinalRef.current = true;
                stopCaptureGraph();
                setTrayRecording(false);
                setErrorInfo({ kind: 'server', message: '' });
                setState('error');
                void finishAttemptOutputSession();
              }
              return;
            }
            if (failRawPcmSession()) return;
            if (
              !wsHadFinalRef.current &&
              mediaRecorderRef.current &&
              mediaRecorderRef.current.state === 'inactive'
            ) {
              if (fallbackTimerRef.current) {
                clearTimeout(fallbackTimerRef.current);
                fallbackTimerRef.current = null;
              }
              sendForTranscription(outputSessionIdRef.current, generation);
            }
          };
          wsRef.current = ws;
        } catch {
          if (!isCurrent()) {
            stopCaptureGraph();
            return;
          }
          wsRef.current = null;
          if (pcmMode) {
            // Raw-PCM has no POST fallback — a socket that can't even be
            // constructed is fatal to the session, so say so instead of
            // recording into the void.
            stream.getTracks().forEach((tr) => tr.stop());
            streamRef.current = null;
            setErrorInfo({ kind: 'server', message: '' });
            setState('error');
            await finishAttemptOutputSession();
            return;
          }
          // Legacy path continues below: the recorder still buffers chunks and
          // the POST /transcribe fallback delivers the result on stop.
          console.warn('ws open failed — will fall back to POST /transcribe');
        }

        if (pcmMode) {
          // Raw-PCM path: stream int16 mono frames at 16 kHz via the AudioWorklet
          // (no MediaRecorder, no WebM POST fallback — the WS is the only channel).
          //   • sherpa live engine → UNTAGGED int16 frames (the non-AEC sherpa
          //     handler reads plain PCM); the far-end bus is NOT subscribed.
          //   • AEC on → frames are 1-byte tagged (0x00 mic / 0x01 far-end) and the
          //     audio player's output is subscribed as the echo reference.
          // Every mic frame also feeds the waveform ring buffer — the pill's
          // bars are computed client-side from the SAME worklet frames (no
          // second audio pipeline).
          const [{ startMicCapture }, { frameFromFloat, floatToInt16, AEC_NEAR, AEC_FAR }] =
            await Promise.all([import('../utils/aec/micCapture'), import('../utils/aec/pcm')]);
          if (!isCurrent()) {
            stopCaptureGraph();
            return;
          }
          const sendBuf = (buf) => {
            if (!isCurrent() || pausedRef.current) return;
            const ws = wsRef.current;
            if (ws && ws.readyState === WebSocket.OPEN) {
              try {
                ws.send(buf);
              } catch (err) {
                // Socket is going down — onclose finalises/recovers the session.
                console.warn('ws send failed:', err);
              }
            } else {
              wsPendingRef.current.push(buf);
            }
          };
          if (aecOn) {
            // Tagged frames + far-end reference (echo cancellation). Works for the
            // sherpa+AEC combo too — the backend demuxes the tag before the
            // sherpa handler sees the cleaned near-end PCM.
            const { subscribeFarEnd } = await import('../utils/aec/farEndBus');
            if (!isCurrent()) {
              stopCaptureGraph();
              return;
            }
            const sendTagged = (float32, kind) => sendBuf(frameFromFloat(float32, kind));
            const stopMicCapture = await startMicCapture(
              stream,
              (f) => {
                if (pausedRef.current) return;
                waveRef.current.push(f);
                sendTagged(f, AEC_NEAR);
              },
              { sampleRate: 16000 },
            );
            if (!isCurrent()) {
              await stopMicCapture();
              stopCaptureGraph();
              return;
            }
            aecStopRef.current = stopMicCapture;
            farEndUnsubRef.current = subscribeFarEnd((f) => sendTagged(f, AEC_FAR));
          } else {
            // Untagged int16 frames for the plain sherpa live path. Send the
            // Int16Array's underlying buffer verbatim (little-endian on every
            // target platform = numpy's native int16 read on the server).
            const stopMicCapture = await startMicCapture(
              stream,
              (f) => {
                if (pausedRef.current) return;
                waveRef.current.push(f);
                const i16 = floatToInt16(f);
                sendBuf(i16.buffer.slice(i16.byteOffset, i16.byteOffset + i16.byteLength));
              },
              { sampleRate: 16000 },
            );
            if (!isCurrent()) {
              await stopMicCapture();
              stopCaptureGraph();
              return;
            }
            aecStopRef.current = stopMicCapture;
          }
          mediaRecorderRef.current = null;
        } else {
          const { recorder, mimeType, extension } = supportedRecorder;
          recordingFormatRef.current = { mimeType, extension };
          mediaRecorderRef.current = recorder;
        }
        // The session may already have RESOLVED while the mic graph was being
        // set up (the awaits above): a connect-time WS error frame (e.g. the
        // typed asr_model_missing preflight) or an Esc-cancel sets
        // wsHadFinalRef and renders the truthful terminal state. Entering
        // 'recording' now would clobber that state and — with the socket gone —
        // strand the next Stop on "Transcribing…" forever. Release the capture
        // inputs and leave the pill alone.
        if (!isCurrent() || wsHadFinalRef.current) {
          stopCaptureGraph();
          return;
        }
        pausedRef.current = false;
        setPaused(false);
        startTimeRef.current = Date.now();
        setTrayRecording(true);
        setWaveOn(pcmMode);
        setBars(Array.from({ length: WAVE_BARS }, () => 0));
        setState('recording');
        setTranscript('');
        setPartialText('');
        setModelStatus(null);
        setErrorInfo(null);
        setDoneKind(null);
        setDuration(0);
        stateRef.current = 'recording';
        if (holdStartRef.current === 'released') {
          holdStartRef.current = null;
          stopRecordingRef.current?.();
        } else {
          holdStartRef.current = null;
        }
      } catch (err) {
        holdStartRef.current = null;
        if (!isCurrent()) {
          stopCaptureGraph();
          return;
        }
        // Same guard as the success path above (#1175 review): the session may
        // already have RESOLVED while setup was failing — a connect-time WS
        // error frame (e.g. the typed asr_model_missing preflight) or an
        // Esc-cancel set wsHadFinalRef and rendered the truthful terminal
        // state. A late mic error must not clobber it.
        if (wsHadFinalRef.current) {
          stopCaptureGraph();
          return;
        }
        stopCaptureGraph();
        // Distinguish "permission denied" (→ per-OS settings hint) from
        // "no device" / "device busy" / anything else (#323).
        toast.error(micErrorMessage(t, err), { duration: 6000 });
        setTrayRecording(false);
        setErrorInfo({
          kind: 'mic',
          message: String(err?.message || err),
          // Permission-denied errors (describeMicError sets a hintKey only for
          // those) get the pill's Open-Settings action inside Tauri.
          deniedByOs: !!describeMicError(err).hintKey,
        });
        setState('error');
        await finishAttemptOutputSession();
      }
    },
    [
      applyResult,
      finalizeSession,
      finishOutputSession,
      liveType,
      queueSegmentPaste,
      stopCaptureGraph,
      t,
    ],
  );

  const startRecording = useCallback(
    async (sessionId = null) => {
      if (startInFlightRef.current) {
        if (sessionId && sessionId !== outputSessionIdRef.current) {
          // Duplicate native events normally carry one id. Defensively adopt a
          // newer non-empty lease without launching a second microphone graph.
          outputSessionIdRef.current = sessionId;
        }
        return false;
      }
      if (inTauri() && !sessionId) {
        hideWidgetWindow();
        return false;
      }
      startInFlightRef.current = true;
      if (sessionId) outputSessionIdRef.current = sessionId;
      const generation = ++captureGenerationRef.current;
      if (dismissTimerRef.current) {
        clearTimeout(dismissTimerRef.current);
        dismissTimerRef.current = null;
      }
      try {
        await startRecordingImpl(generation, sessionId);
      } finally {
        startInFlightRef.current = false;
        if (generation !== captureGenerationRef.current) {
          pendingNativeStartRef.current = null;
        } else {
          const pending = pendingNativeStartRef.current;
          if (pending) {
            if (
              pending.sequence !== nativeStartSequenceRef.current ||
              outputSessionIdRef.current !== pending.sessionId
            ) {
              pendingNativeStartRef.current = null;
            } else {
              const current = stateRef.current;
              pendingNativeStartRef.current = null;
              if (current !== 'recording' && current !== 'transcribing') {
                await startRecordingRef.current?.(pending.trackHold, pending.sessionId);
              }
            }
          }
        }
      }
      return stateRef.current === 'recording' || stateRef.current === 'transcribing';
    },
    [startRecordingImpl],
  );

  const togglePause = useCallback(() => {
    if (stateRef.current !== 'recording') return;
    const next = !pausedRef.current;
    const recorder = mediaRecorderRef.current;
    if (recorder?.state === 'recording' && next) recorder.pause();
    else if (recorder?.state === 'paused' && !next) recorder.resume();
    pausedRef.current = next;
    streamRef.current?.getTracks().forEach((track) => {
      track.enabled = !next;
    });
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) ws.send(next ? 'PAUSE' : 'RESUME');
    setPaused(next);
  }, []);

  const stopRecording = useCallback(() => {
    const generation = captureGenerationRef.current;
    const sessionId = outputSessionIdRef.current;
    stopCaptureGraph();
    // Signal EOF to WebSocket
    const ws = wsRef.current;
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      const sendEof = () => {
        if (generation !== captureGenerationRef.current || wsRef.current !== ws) return;
        try {
          ws.send('EOF');
        } catch (err) {
          // Socket died before EOF — the fallback timer / onclose recovers.
          console.warn('ws EOF send failed:', err);
        }
      };
      if (ws.readyState === WebSocket.OPEN) {
        sendEof();
      } else {
        ws.addEventListener('open', sendEof, { once: true });
      }
      // Fallback timer
      const recorded = startTimeRef.current ? Date.now() - startTimeRef.current : 0;
      const ms = Math.max(15000, recorded + 10000);
      if (fallbackTimerRef.current) clearTimeout(fallbackTimerRef.current);
      fallbackTimerRef.current = setTimeout(() => {
        fallbackTimerRef.current = null;
        if (!wsHadFinalRef.current) {
          wsRef.current?.close();
          wsRef.current = null;
          sendForTranscription(sessionId, generation);
        }
      }, ms);
    }
    setTrayRecording(false);
    setState('transcribing');
  }, [stopCaptureGraph]);

  const sendForTranscription = useCallback(
    async (sessionId = outputSessionIdRef.current, generation = captureGenerationRef.current) => {
      const isCurrent = () =>
        generation === captureGenerationRef.current &&
        (!sessionId || outputSessionIdRef.current === sessionId);
      if (!isCurrent() || wsHadFinalRef.current) return;
      // No encoded blob exists on a raw-PCM path — the WS is the only result
      // channel there.
      if (aecModeRef.current || sherpaModeRef.current || pcmModeRef.current) return;

      const { mimeType, extension } = recordingFormatRef.current;
      const blob = new Blob(chunksRef.current, { type: mimeType });
      const formData = new FormData();
      formData.append('audio', blob, `capture.${extension}`);
      formData.append('mode', captureMode);

      try {
        // apiFetch attaches the PIN / remote API key headers (Wave 2.3)
        // and throws on non-2xx with the server's detail message.
        const res = await apiFetch('/transcribe', {
          method: 'POST',
          body: formData,
        });
        if (!isCurrent()) return;
        const data = await res.json();
        if (!isCurrent() || wsHadFinalRef.current) return;
        await applyResult(data, sessionId, generation);
      } catch (err) {
        if (!isCurrent() || wsHadFinalRef.current) return;
        const missing = asrMissingPayload(err);
        if (missing) {
          // Typed 409: no ASR model installed → download CTA, not a dead end.
          if (!inTauri()) toastAsrModelMissing(missing);
          setErrorInfo({ kind: 'asr_missing', message: t('asr_missing.message'), missing });
          setState('error');
          setTranscript('');
          await finishOutputSession(sessionId);
          if (captureGenerationRef.current !== generation) return;
          return;
        }
        toast.error(t('capture.transcription_failed', { message: err.message }));
        setErrorInfo({ kind: 'transcription', message: err.message });
        setState('error');
        setTranscript('');
        await finishOutputSession(sessionId);
      }
    },
    [captureMode, applyResult, finishOutputSession, t],
  );

  // Keep the trigger refs pointing at the current callbacks. No dep array: it
  // must run after every render so the once-attached tray listener above never
  // calls into a stale closure.
  useEffect(() => {
    startRecordingRef.current = (trackHold = false, sessionId = null) => {
      if (trackHold && holdStartRef.current !== 'released') holdStartRef.current = 'starting';
      return startRecording(sessionId);
    };
    stopRecordingRef.current = stopRecording;
  });

  // Safety net: the widget window must never sit on screen with nothing in it.
  //
  // Visibility is decided in Rust (lib.rs shows the window on the global
  // shortcut, before emitting `tray-dictate`) but content is decided here, and
  // nothing kept the two in agreement. Any path that shows the window without
  // the pill reaching a drawing state — a dropped event, a hotkey pressed
  // while dictation is disabled, a startRecording() that bails early — left an
  // empty capsule stranded on the desktop. It could not be dismissed either:
  // `idle` renders null so there is no X button, and the Esc handler never
  // fires because the widget deliberately refuses focus on macOS and Windows
  // (#287, #982).
  //
  // Rather than patch each call site, converge on the invariant itself: if we
  // are idle and the window is still visible a beat later, hide it. One effect
  // covers every path that exists now and every one added later.
  // A one-shot timer keyed on the transition into `idle` is not enough: the
  // window is shown by the Rust side, and `state` does not change when a press
  // is dropped. So a press arriving while we are ALREADY idle shows the window
  // without re-running this effect, and the square strands exactly as before —
  // the same bug, one path over. Poll instead, so the invariant holds no matter
  // who made the window visible or when (CodeRabbit, #1399).
  // Every state but `idle` is one the user is meant to see — listening,
  // transcribing, the result flash, an error, the Accessibility prompt. Show
  // the window here rather than at each call site, for the same reason the
  // idle reconcile below hides it here: one invariant covers the paths that
  // exist now and the ones added later. `dismiss()` owns the hide.
  useEffect(() => {
    if (state === 'idle') return;
    showWidgetWindow();
  }, [state]);

  useEffect(() => {
    if (state !== 'idle' || !inTauri()) return undefined;
    let cancelled = false;
    // Grace is measured from when the window was first SEEN visible-while-idle,
    // not from the effect mounting: a real dictation shows the window a beat
    // before React flips out of `idle`, and hiding it in that gap would cancel
    // the very session the user just started.
    let visibleSince = 0;

    const reconcile = async () => {
      // A positively-hidden document cannot be a stranded pill, and skipping
      // the IPC keeps the common case free. Platforms that never report hidden
      // just pay for the check — correct either way.
      if (typeof document !== 'undefined' && document.hidden) {
        visibleSince = 0;
        return;
      }
      try {
        const { getCurrentWindow } = await import('@tauri-apps/api/window');
        const win = getCurrentWindow();
        // Only the standalone widget window owns its own visibility; the
        // in-app pill is just a DOM node in the main window.
        if (cancelled || win.label !== 'widget') return;
        const visible = await win.isVisible();
        // Re-check AFTER the IPC. The thing that tears this effect down is
        // recording starting — so a continuation resuming here is, precisely,
        // the case where hiding would take the pill off screen at the moment
        // the user began speaking. One check covers the rest of the function:
        // nothing below awaits again before `hide()` (CodeRabbit, #1399).
        if (cancelled) return;
        if (!visible) {
          visibleSince = 0;
          return;
        }
        const now = Date.now();
        if (!visibleSince) {
          visibleSince = now;
          return;
        }
        if (now - visibleSince < IDLE_VISIBLE_GRACE_MS) return;
        console.warn('capture: widget window visible while idle — hiding it');
        await win.hide();
        visibleSince = 0;
      } catch (err) {
        console.warn('capture: idle-visibility reconcile failed:', err);
      }
    };

    const id = setInterval(reconcile, IDLE_VISIBLE_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [state]);

  // The pill carries these states on screen now, but it is transient and
  // deliberately unfocusable. States that need the user to DO something —
  // grant Accessibility, grant the mic, retry after a failure — also go to the
  // main window, where the instruction survives the pill's auto-dismiss and
  // can be acted on.
  useEffect(() => {
    if (state !== 'error' && state !== 'setup') return;
    const kind = state === 'setup' ? 'setup' : errorInfo?.kind || 'transcription';
    emitDictationNotice({
      kind,
      // Localize here: this is where the error's context lives, and both
      // windows share one i18n instance and language.
      label: state === 'setup' ? t('capture.a11y_setup') : errorLabel(t, errorInfo),
      // Only an OS-level denial has a settings pane worth opening. A mic that
      // is merely busy or absent fails with the same kind, and sending that
      // user to the permissions pane sends them somewhere nothing is wrong —
      // the same condition the pill's own mic button carried.
      deniedByOs: !!errorInfo?.deniedByOs,
      // The install recommendation, so the main window can offer the one-click
      // download the pill has no room for.
      missing: errorInfo?.missing,
    });
  }, [state, errorInfo, t]);

  // Idle: render nothing — pill is hold-to-talk only (Whisper-Flow / Ghost-Pepper
  // style). The tray-dictate listener above stays mounted, so the shortcut still
  // triggers startRecording() which flips state out of 'idle' and remounts the
  // pill DOM with the slide-in animation.
  if (state === 'idle') return null;

  // ── Pill label ──
  let label = '';
  let emoji = '';
  if (state === 'recording' && paused) {
    label = t('common.paused');
  } else if (state === 'setup') {
    // One-time Accessibility setup — shown instead of pretending to work.
    emoji = '🔒';
    label = t('capture.a11y_setup');
  } else if (modelStatus && (state === 'recording' || state === 'transcribing')) {
    // Backend model lifecycle beats the generic listening/transcribing labels.
    emoji = modelStatus.stage === 'downloading' ? '⏬' : '⏳';
    label =
      modelStatus.stage === 'downloading'
        ? modelStatus.progress != null
          ? t('capture.model_downloading_pct', {
              percent: Math.round(modelStatus.progress * 100),
            })
          : t('capture.model_downloading')
        : t('capture.model_loading');
  } else if (state === 'recording') {
    emoji = '🎙️';
    label = partialText || t('capture.listening_label');
  } else if (state === 'transcribing') {
    emoji = '📝';
    label = partialText || t('capture.transcribing_label');
  } else if (state === 'done' && transcript) {
    emoji = '✅';
    label =
      doneKind === 'copied'
        ? t('capture.copied')
        : doneKind === 'inserted'
          ? t('capture.inserted')
          : t('capture.pasted');
  } else if (state === 'done' && !transcript) {
    emoji = '⚠️';
    label = t('capture.no_speech');
  } else if (state === 'error') {
    emoji = '❌';
    label = errorLabel(t, errorInfo);
  }

  const showA11yAction = state === 'setup' || (state === 'error' && errorInfo?.kind === 'a11y');
  // OS-level mic denial gets its own Open-Settings deep-link (Tauri only —
  // a browser denial has no OS pane we can open).
  const showMicAction =
    state === 'error' && errorInfo?.kind === 'mic' && errorInfo?.deniedByOs && inTauri();

  return (
    <div
      className={`capture-pill capture-pill--${state === 'recording' && paused ? 'paused' : state}`}
      role="status"
      aria-live="polite"
    >
      {/* Live waveform while the worklet feeds us; pulsing dot otherwise */}
      {state === 'recording' && !paused && waveOn && !modelStatus ? (
        <div className="capture-pill__wave" aria-hidden="true">
          {bars.map((v, i) => (
            <span
              key={i}
              className="capture-pill__wave-bar"
              style={{ height: `${Math.round(12 + v * 88)}%` }}
            />
          ))}
        </div>
      ) : (
        <span className="capture-pill__dot" />
      )}

      {/* Content */}
      <div className="capture-pill__preview">
        <span
          className="block text-[14px] leading-[1.5] font-medium"
          title={state === 'error' ? errorInfo?.message || label || undefined : label || undefined}
        >
          {emoji} {label}
        </span>
      </div>

      {/* Timer */}
      {(state === 'recording' || state === 'transcribing') && !modelStatus && (
        <span className="shrink-0 font-mono text-[11px] font-medium tracking-[0.03em] text-white/50">
          {formatElapsed(duration)}
        </span>
      )}

      {/* Spinner while transcribing or while the model downloads/loads */}
      {(state === 'transcribing' || (state === 'recording' && modelStatus)) && (
        <Loader size={14} className="shrink-0 text-white/40 motion-safe:animate-spin" />
      )}

      {/* Accessibility action — setup state and a11y-kind paste errors */}
      {showA11yAction && (
        <button
          className="shrink-0 cursor-pointer whitespace-nowrap rounded-full border-0 bg-white/[0.1] px-2.5 py-1 text-[11px] font-medium text-white/90 transition-[background] duration-[0.15s] hover:bg-white/[0.18]"
          onClick={openA11ySettings}
        >
          {t('capture.open_a11y_settings')}
        </button>
      )}

      {/* Microphone action — OS-denied mic errors deep-link the mic pane */}
      {showMicAction && (
        <button
          className="shrink-0 cursor-pointer whitespace-nowrap rounded-full border-0 bg-white/[0.1] px-2.5 py-1 text-[11px] font-medium text-white/90 transition-[background] duration-[0.15s] hover:bg-white/[0.18]"
          onClick={async () => {
            if (!(await openMicrophoneSettings())) {
              // Linux: no mic-privacy pane — point at system sound settings.
              toast(t('capture.mic_hint_linux'), { icon: 'ℹ️', duration: 8000 });
            }
          }}
        >
          {t('permissions.open_settings')}
        </button>
      )}

      {state === 'recording' && (
        <>
          <button
            type="button"
            className="capture-pill__control"
            onClick={togglePause}
            aria-label={t(paused ? 'common.resume' : 'common.pause')}
            title={t(paused ? 'common.resume' : 'common.pause')}
          >
            {paused ? <Play size={14} /> : <Pause size={14} />}
          </button>
          <button
            type="button"
            className="capture-pill__control"
            onClick={stopRecording}
            aria-label={t('common.stop')}
            title={t('common.stop')}
          >
            <Square size={12} />
          </button>
        </>
      )}
      {(state === 'recording' || state === 'transcribing') && (
        <button
          type="button"
          className="capture-pill__control"
          onClick={cancelSession}
          aria-label={t('common.close')}
          title={t('common.close')}
        >
          <X size={14} />
        </button>
      )}
      {/* Dismiss — done/error/setup */}
      {(state === 'done' || state === 'error' || state === 'setup') && (
        <button
          className="flex h-[20px] w-[20px] shrink-0 cursor-pointer items-center justify-center rounded-full border-0 bg-white/[0.06] p-0 text-white/40 transition-[background,color] duration-[0.15s] hover:bg-white/[0.12] hover:text-white/80"
          onClick={dismiss}
          aria-label={t('common.dismiss')}
        >
          <X size={12} />
        </button>
      )}
    </div>
  );
}
