/**
 * User-preference slice — translateQuality, dualSubs, etc.
 *
 * These were `useState(() => localStorage.getItem(...))` scattered through
 * App.jsx. Centralising them in the store lets any component read/write
 * without prop-drilling and lets zustand's `persist` middleware handle
 * the storage round-trip once instead of per-field.
 */
import type { StateCreator } from 'zustand';
import { apiJson, apiPost } from '../api/client';

type TranslateQuality = 'fast' | 'autofit' | 'cinematic';
type ThemeId = 'gruvbox' | 'midnight' | 'nord' | 'solarized' | 'rose-pine' | 'catppuccin';

/** Dictation start/stop semantics — mirror of the backend `dictation.mode`. */
type DictationMode = 'toggle' | 'hold';

/** Default sherpa dictation model id — matches the backend
 * `sherpa_dictation.DEFAULT_MODEL_ID`. Used only as the pre-hydration seed;
 * the authoritative value comes from `GET /dictation/prefs`. */
const DEFAULT_DICTATION_MODEL_ID = 'sherpa-whisper-tiny';

/**
 * Global UI font. Applied app-wide by overriding the `--font-sans` CSS custom
 * property on `document.documentElement` (the whole UI uses
 * `font-family: var(--font-sans)`). `default` removes the override so the CSS
 * `:root` Inter stack takes over. All stacks are SYSTEM-SAFE — no web-font
 * downloads, so this works identically offline across macOS/Windows/Linux.
 */
type FontId = 'default' | 'system' | 'serif' | 'mono' | 'rounded' | 'readable';

export const FONT_OPTIONS: { id: FontId; label: string }[] = [
  { id: 'default', label: 'Inter (default)' },
  { id: 'system', label: 'System' },
  { id: 'serif', label: 'Serif' },
  { id: 'mono', label: 'Monospace' },
  { id: 'rounded', label: 'Rounded' },
  { id: 'readable', label: 'Readable' },
];

export const FONT_STACKS: Record<FontId, string | null> = {
  default: null, // use the CSS :root --font-sans (Inter)
  system: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
  serif: 'Georgia, "Times New Roman", serif',
  mono: 'ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, monospace',
  rounded: '"SF Pro Rounded", "Nunito", "Quicksand", system-ui, sans-serif',
  readable: '"Atkinson Hyperlegible", Verdana, system-ui, sans-serif',
};

/**
 * Dub timing strategy — replaces audio time-compression with cleaner
 * alternatives. `concise` trims the translation up-front so it fits at
 * natural rate (overflows surfaced for manual edit); `smart_fit` splits
 * the burden between a mild audio speed-up (≤1.2× alone, ≤1.5× hybrid)
 * and a mild per-segment video slow-down (≤2.0×); `stretch_video`
 * stretches the source video per-segment so natural-rate audio fits
 * without lip-sync drift. `strict_slot` is the legacy compress-to-fit
 * path, retained for back-compat.
 */
type TimingStrategy = 'concise' | 'smart_fit' | 'stretch_video' | 'strict_slot';

/**
 * Dub voice-identity mode (DubRequest.voice_match). `per_line` (default —
 * unchanged Wave 3.2 behaviour) clones each line from a reference cut from
 * its OWN source audio: best prosody match, but the voice identity can drift
 * line to line. `consistent` resolves every line of a speaker to ONE
 * reference — the pooled speaker clone, or (heuristic diarization, where no
 * speaker clones exist) one deterministic best clip — for a steady identity.
 */
type VoiceMatch = 'per_line' | 'consistent';

/**
 * Knob overrides for the `smart_fit` strategy. `null` (default) sends no
 * `fit_options` and the backend uses its canonical FitParams defaults —
 * identical behavior on every platform out of the box.
 */
interface FitOptions {
  max_audio_only_rate?: number;
  audio_rate_cap?: number;
  video_slow_cap?: number;
  gap_guard_s?: number;
  allow_video_retime?: boolean;
}

export interface PrefsSlice {
  translateQuality: TranslateQuality;
  /**
   * Opt-in LLM condensation for doomed dub segments (default OFF). When on,
   * Translate asks the configured LLM for a shorter meaning-preserving
   * rewrite of every segment the duration planner marks "impossible" and
   * attaches it as a per-segment suggestion — never applied automatically.
   */
  condenseSuggest: boolean;
  dualSubs: boolean;
  burnSubs: boolean;
  /**
   * Hardsub caption style: when burn-in is on, burn a word-timed karaoke
   * highlight (ASS) instead of static line subtitles. Default OFF — the line
   * burn stays the out-of-the-box behaviour. Ignored while dualSubs is on
   * (dual-layout karaoke is unsupported; the Export drawer disables the
   * control and the backend renders the line burn).
   */
  karaokeSubs: boolean;
  glossaryVisible: boolean;
  /**
   * Phase 4.3 — staged checkpoints. When 'on', between-stage banners nudge
   * the user to review ASR / translation output before advancing. Turn 'off'
   * for rapid-fire workflows where reviewing every stage is overkill.
   */
  reviewMode: 'on' | 'off';

  /**
   * Show RAM/CPU/VRAM live counters in the header. Default OFF — the
   * "Make voices that sound like you" landing screen shouldn't double as a
   * resource monitor. Power users can flip this on via Settings →
   * Performance. The Idle/Ready/Loading status badge + Flush button stay
   * visible regardless because they're action-relevant.
   */
  showHeaderLiveStats: boolean;

  /**
   * How the dub pipeline reconciles natural-rate TTS with the original
   * timeline. `strict_slot` (default) compresses audio to fit; `concise`
   * trims translation while preserving natural-rate audio; `stretch_video`
   * stretches the video instead.
   */
  timingStrategy: TimingStrategy;

  /**
   * Optional Smart Fit knob overrides. Stays `null` unless a power user
   * sets custom caps — the backend then applies its own defaults.
   */
  fitOptions: FitOptions | null;

  /** Dub voice-identity mode — see the VoiceMatch type doc. */
  voiceMatch: VoiceMatch;

  /**
   * Opt-in live dub preview (default OFF). When on, editing a segment's
   * translated text streams TTS for that line over the existing `/ws/tts`
   * socket (debounced, local playback only) so the user hears the edit
   * without pressing Generate. Never persisted as job audio — export still
   * goes through the full-quality generate path.
   */
  dubLivePreview: boolean;
  setDubLivePreview: (on: boolean) => void;

  /**
   * Last app version whose release notes the user has seen (feat/safe-updates).
   * `null` = never recorded (fresh install / pre-feature profile): the first
   * launch baselines it silently so brand-new users don't get a "What's new"
   * nudge for a version they just installed. After an update,
   * `whatsNewPending()` flags the mismatch and the footer shows a small
   * non-blocking "What's new" affordance until the notes are opened.
   */
  whatsNewSeenVersion: string | null;
  setWhatsNewSeenVersion: (v: string | null) => void;

  /**
   * System-notification ids the user has dismissed (bell + footer tab).
   * Only info/warn notes are dismissible — errors describe conditions that
   * need fixing and stay visible until the condition clears. Ids are stable
   * per condition (e.g. `gpu-unavailable` on a CPU-only box), so a dismissal
   * is durable across sessions; notes whose id encodes an occurrence (the
   * `last-run-crash-<ts>` family) naturally re-notify as a fresh id when
   * they recur. Capped so the list can't grow unbounded.
   */
  dismissedNotificationIds: string[];
  dismissNotification: (id: string) => void;

  /**
   * LLM dub engine — auto-glossary. One up-front LLM pass over the whole
   * transcript extracts a theme summary + terminology map, merged with the
   * manual glossary (manual entries always win) and injected into every
   * segment's translation prompt so names/terms stay consistent. Only applies
   * when the translation engine is the LLM one. Default ON.
   */
  autoGlossary: boolean;
  setAutoGlossary: (on: boolean) => void;

  /**
   * LLM dub engine — reflect pass. After each segment's direct translation,
   * a critique-then-rewrite step polishes wordy/stiff lines into natural
   * spoken dialogue. Costs 3 LLM calls per segment instead of 1; any failure
   * silently keeps the direct translation. Default ON.
   */
  reflectPass: boolean;
  setReflectPass: (on: boolean) => void;

  setTranslateQuality: (q: TranslateQuality) => void;
  setCondenseSuggest: (on: boolean) => void;
  setDualSubs: (on: boolean) => void;
  setBurnSubs: (on: boolean) => void;
  setKaraokeSubs: (on: boolean) => void;
  setGlossaryVisible: (on: boolean) => void;
  setReviewMode: (mode: 'on' | 'off') => void;
  setShowHeaderLiveStats: (on: boolean) => void;
  setTimingStrategy: (s: TimingStrategy) => void;
  setFitOptions: (o: FitOptions | null) => void;
  setVoiceMatch: (m: VoiceMatch) => void;

  /**
   * Opt-in dictate-over-playback echo cancellation (parity Action 8). When
   * on, dictation streams raw PCM through the server-side NLMS AEC and the
   * audio player taps its output as the echo reference, so dictating while
   * VoiceStudio plays audio doesn't transcribe the playback. Default OFF — the
   * standard MediaRecorder dictation path is unchanged when off.
   */
  aecEnabled: boolean;
  setAecEnabled: (on: boolean) => void;

  /**
   * Live-dictation prefs — MIRRORED from the backend `GET /dictation/prefs`
   * (the backend `prefs.json` `dictation.*` namespace is the source of truth).
   * On app init we hydrate these from the backend; every setter write-throughs
   * to `POST /dictation/prefs` so the capture engine and the UI never diverge.
   * They're intentionally NOT in `partialize` (store/index.ts) — the backend
   * owns them, so persisting a stale localStorage copy would fight the server.
   *
   *   • dictationEnabled  — master on/off for the dictation hotkey.
   *   • dictationMode     — 'toggle' (press to start, press to stop) | 'hold'
   *                          (record while the key is held).
   *   • dictationModelId  — the selected sherpa-onnx model id (e.g.
   *                          'sherpa-whisper-tiny'); drives `?model=` on the
   *                          live `/ws/transcribe` socket.
   */
  dictationEnabled: boolean;
  dictationMode: DictationMode;
  dictationModelId: string;
  /** Local-only flag: true once the backend prefs have been hydrated, so the
   * Voice panel can avoid flashing defaults before the first load. */
  dictationLoaded: boolean;
  /** Optimistic local set + write-through to POST /dictation/prefs. */
  setDictationEnabled: (on: boolean) => void;
  setDictationMode: (mode: DictationMode) => void;
  setDictationModelId: (id: string) => void;
  /** Hydrate from GET /dictation/prefs (called once on app init). */
  /** Resolves true when the backend answered, false when the seeds were
   *  kept because it did not. `dictationLoaded` is set either way (so the
   *  Settings panel never spins forever); this is how a caller can tell
   *  the two apart and retry. */
  loadDictationPrefs: () => Promise<boolean>;

  /**
   * Auto-play the output preview as soon as a render finishes (Voice Clone /
   * Design / profile preview, AND the studio generate path in useTTS —
   * #1032 wired the latter; #666's toggle only covered the WaveformPlayer
   * sites). Default ON — preserves the long-standing behavior. Users
   * batch-generating segments (#666) can turn it off so each finished clip
   * doesn't start playing on its own.
   */
  autoPlayPreview: boolean;
  setAutoPlayPreview: (on: boolean) => void;

  locale: string;
  setLocale: (l: string) => void;

  /**
   * True once the user has made an EXPLICIT UI-language choice — via the
   * Settings → General picker or the first-run "Switch to English?" offer.
   * The `locale` field always has a value (navigator-derived on a fresh
   * install), so it can't distinguish "detected" from "chosen"; this flag can.
   * Gates the first-run English offer so it never shows once a user has ever
   * picked a language. Default false; set true by `setLocale`.
   */
  localeChosen: boolean;

  /**
   * True once the first-run "Switch to English?" offer has been shown and
   * acted on or dismissed. One-time: guarantees the offer never reappears
   * after the user answered it (or kept their language) once.
   */
  langPromptSeen: boolean;
  setLangPromptSeen: (seen: boolean) => void;

  /**
   * Force reduced motion regardless of the OS setting.
   *
   * The CSS already honours `prefers-reduced-motion` in a dozen places, but
   * that is the OS switch and nothing else. Someone who wants a calm app
   * without turning motion off system-wide had no way to ask for it, and
   * someone whose OS setting is not respected by their environment had no
   * recourse at all (#1857). This is additive: the media query still applies
   * on its own, so turning this off never re-enables motion for a user whose
   * OS asked for less.
   */
  reduceMotion: boolean;
  setReduceMotion: (on: boolean) => void;

  theme: ThemeId;
  setTheme: (id: ThemeId) => void;

  font: FontId;
  setFont: (id: FontId) => void;
}

/** Map a `GET/POST /dictation/prefs` response → the store's dictation fields.
 * Tolerant of partial/garbage payloads so a malformed response can never wedge
 * the store. */
function _dictationFromPrefs(p: any): Partial<PrefsSlice> {
  const out: Partial<PrefsSlice> = {};
  if (p && typeof p === 'object') {
    if (typeof p.enabled === 'boolean') out.dictationEnabled = p.enabled;
    if (p.mode === 'toggle' || p.mode === 'hold') out.dictationMode = p.mode;
    if (typeof p.model_id === 'string' && p.model_id) out.dictationModelId = p.model_id;
  }
  return out;
}

export const createPrefsSlice: StateCreator<PrefsSlice, [], [], PrefsSlice> = (set, get) => ({
  translateQuality: 'fast',
  autoGlossary: true,
  reflectPass: true,
  condenseSuggest: false,
  dualSubs: false,
  burnSubs: false,
  karaokeSubs: false,
  glossaryVisible: true,
  reviewMode: 'on',
  showHeaderLiveStats: false,
  timingStrategy: 'strict_slot',
  fitOptions: null,
  voiceMatch: 'per_line',
  dubLivePreview: false,
  whatsNewSeenVersion: null,
  dismissedNotificationIds: [],
  aecEnabled: false,
  autoPlayPreview: true,

  // Seeds only — overwritten by loadDictationPrefs() on init. The backend
  // default is enabled:true / mode:'toggle' / model:Whisper Tiny.
  dictationEnabled: true,
  dictationMode: 'toggle',
  dictationModelId: DEFAULT_DICTATION_MODEL_ID,
  dictationLoaded: false,

  setTranslateQuality: (q) => set({ translateQuality: q }),
  setAutoGlossary: (on) => set({ autoGlossary: on }),
  setReflectPass: (on) => set({ reflectPass: on }),
  setCondenseSuggest: (on) => set({ condenseSuggest: on }),
  setDualSubs: (on) => set({ dualSubs: on }),
  setBurnSubs: (on) => set({ burnSubs: on }),
  setKaraokeSubs: (on) => set({ karaokeSubs: on }),
  setGlossaryVisible: (on) => set({ glossaryVisible: on }),
  setReviewMode: (mode) => set({ reviewMode: mode }),
  setShowHeaderLiveStats: (on) => set({ showHeaderLiveStats: on }),
  setTimingStrategy: (s) => set({ timingStrategy: s }),
  setFitOptions: (o) => set({ fitOptions: o }),
  setVoiceMatch: (m) => set({ voiceMatch: m }),
  setDubLivePreview: (on) => set({ dubLivePreview: on }),
  setWhatsNewSeenVersion: (v) => set({ whatsNewSeenVersion: v }),
  dismissNotification: (id) =>
    set((s) => ({
      // Dedupe + keep the newest 50: stable ids make re-dismissal a no-op,
      // and occurrence-stamped ids (last-run-crash-<ts>) age out the oldest.
      dismissedNotificationIds: [...s.dismissedNotificationIds.filter((x) => x !== id), id].slice(
        -50,
      ),
    })),
  setAecEnabled: (on) => set({ aecEnabled: on }),
  setAutoPlayPreview: (on) => set({ autoPlayPreview: on }),

  // ── Dictation prefs (backend-backed) ──────────────────────────────────
  // Each setter is optimistic (update the store immediately so the UI is
  // snappy) then write-throughs to POST /dictation/prefs, which returns the
  // full authoritative prefs — we re-sync from that so a backend rejection
  // (400 on a bad value) or normalisation (repo_id → canonical id) can't leave
  // the UI out of step. A failed write rolls the optimistic value back.
  setDictationEnabled: (on) => {
    const prev = get().dictationEnabled;
    set({ dictationEnabled: on });
    apiPost('/dictation/prefs', { enabled: on })
      .then((p: any) => set(_dictationFromPrefs(p)))
      .catch(() => set({ dictationEnabled: prev }));
  },
  setDictationMode: (mode) => {
    const prev = get().dictationMode;
    set({ dictationMode: mode });
    apiPost('/dictation/prefs', { mode })
      .then((p: any) => set(_dictationFromPrefs(p)))
      .catch(() => set({ dictationMode: prev }));
  },
  setDictationModelId: (id) => {
    const prev = get().dictationModelId;
    set({ dictationModelId: id });
    apiPost('/dictation/prefs', { model_id: id })
      .then((p: any) => set(_dictationFromPrefs(p)))
      .catch(() => set({ dictationModelId: prev }));
  },
  loadDictationPrefs: async () => {
    try {
      const p = await apiJson<any>('/dictation/prefs');
      set({ ..._dictationFromPrefs(p), dictationLoaded: true });
      return true;
    } catch {
      // Backend not ready / older build without the route — keep the seeds and
      // mark loaded so the panel renders defaults rather than a perpetual
      // spinner. A later manual interaction will retry the write-through.
      set({ dictationLoaded: true });
      return false;
    }
  },

  locale:
    typeof navigator !== 'undefined'
      ? (() => {
          const nav = navigator.language || '';
          if (nav.toLowerCase().includes('tw') || nav.toLowerCase().includes('hk')) return 'zh-TW';
          const match = [
            'zh-CN',
            'es',
            'fr',
            'de',
            'ja',
            'pt',
            'it',
            'ru',
            'ko',
            'hi',
            'tr',
            'pl',
            'nl',
            'sv',
            'th',
            'vi',
            'id',
            'uk',
            'ar',
          ].find((code) => nav.startsWith(code.split('-')[0]));
          return match || 'en';
        })()
      : 'en',
  // Any setLocale call is a deliberate user choice (the only callers are the
  // Settings picker and the first-run English offer), so record it as such —
  // this is what tells the first-run offer "the user already decided".
  setLocale: (l) => set({ locale: l, localeChosen: true }),

  localeChosen: false,
  langPromptSeen: false,
  setLangPromptSeen: (seen) => set({ langPromptSeen: seen }),

  reduceMotion: false,
  setReduceMotion: (on) => {
    set({ reduceMotion: on });
    if (on) {
      document.documentElement.setAttribute('data-motion', 'reduce');
    } else {
      document.documentElement.removeAttribute('data-motion');
    }
  },

  theme: 'gruvbox',
  setTheme: (id) => {
    set({ theme: id });
    // Apply to DOM — gruvbox is default (no attribute)
    if (id === 'gruvbox') {
      document.documentElement.removeAttribute('data-theme');
    } else {
      document.documentElement.setAttribute('data-theme', id);
    }
  },

  font: 'default',
  setFont: (id) => {
    set({ font: id });
    const stack = FONT_STACKS[id];
    if (stack) document.documentElement.style.setProperty('--font-sans', stack);
    else document.documentElement.style.removeProperty('--font-sans');
  },
});
