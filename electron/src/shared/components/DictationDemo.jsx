/**
 * DictationDemo — guided walkthrough for the real-time dictation feature.
 *
 * What this surfaces:
 *   1. Active hotkey display (including a portal-selected Wayland shortcut).
 *   2. Three script cards — short utterances the user can read aloud OR
 *      replay from a bundled WAV. The replay path posts the bundled audio
 *      to POST /transcribe and renders the recognized text below the card
 *      so dictation can be demoed even when the user hasn't granted mic
 *      permission yet, or is on a headless / VM / CI box.
 *   3. Hotkey verification status — subscribes to the `tray-dictate` and
 *      `tray-dictate-stop` Tauri events so we can show "verified" the
 *      moment the user presses the shortcut for the first time.
 *
 * Cross-platform: the replay path uses the existing backend transcribe
 * endpoint and works identically on macOS / Windows / Linux. The hotkey
 * verification path requires Tauri (gracefully no-ops in the web UI).
 *
 * Where this is mounted:
 *   - Settings → Capture & Dictation (above HotkeyTab) — always available
 *   - SetupWizard step 4 — first-run onboarding
 */
import { useEffect, useRef, useState } from 'react';
import { Play, Pause, Keyboard, Mic, CheckCircle2, AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { API, apiFetch } from '../api/client';
import { asrMissingPayload, toastAsrModelMissing } from '../utils/asrModelMissing';
import { useEffectiveDictationShortcut } from '../hooks/useEffectiveDictationShortcut';
import { useDictationReadiness } from '../hooks/useDictationReadiness';
import AsrModelChooser from './AsrModelChooser';
import { Button } from '../ui';

// Shared status-pill base; per-state color/bg/border appended below. The gruvbox
// status hues are intentionally preserved (palette kept) as arbitrary utilities.
const STATUS_BASE =
  'inline-flex items-center gap-[6px] text-[11px] px-[8px] py-[3px] rounded-[999px] border';

const SCRIPTS = [
  {
    id: 'en_conversational',
    labelKey: 'demo.script_conversational',
    language: 'English',
    text: 'Schedule a meeting with Pat for Tuesday at three PM and remind me to bring the quarterly report.',
    wav: '/demo_audio/dictation/en_conversational.wav',
  },
  {
    id: 'en_technical',
    labelKey: 'demo.script_technical',
    language: 'English',
    text: 'Patch the WebGPU shader in renderer.tsx, then bump pnpm to nine point fifteen and rerun the Vitest suite.',
    wav: '/demo_audio/dictation/en_technical.wav',
  },
  {
    id: 'fr_reservation',
    labelKey: 'demo.script_french',
    language: 'French',
    text: 'Bonjour, je voudrais réserver une table pour deux personnes à vingt heures.',
    wav: '/demo_audio/dictation/fr_reservation.wav',
  },
];

function isTauri() {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

export default function DictationDemo({ embedded = false }) {
  const { t } = useTranslation();
  const [hotkeyState, setHotkeyState] = useState('unknown'); // unknown | registered | verified
  const desktop = isTauri();
  const { info: shortcut } = useEffectiveDictationShortcut(desktop);
  // Transcribing a sample needs a speech-to-text model, and the mandatory-only
  // install path ships none. Rendering the cards regardless meant the final
  // onboarding step opened with one hard error per card and invited the user
  // to press a hotkey that could not work (#1856). Probed for the same reason
  // the demo already probes for its sample WAVs below.
  const readiness = useDictationReadiness();
  const [playingId, setPlayingId] = useState(null);
  const [transcripts, setTranscripts] = useState({}); // {scriptId: {state, text, error}}
  // null = probing, true/false once the demo assets are confirmed present.
  // The sample WAVs are rendered by scripts/build_demos.sh and may be absent
  // (e.g. source checkout without a render step). When absent we hide the demo
  // rather than show cards that fail on click (#119/#124 follow-up).
  const [assetsAvailable, setAssetsAvailable] = useState(null);
  const audioRef = useRef(null);

  // Probe whether the bundled dictation samples actually exist; hide the whole
  // demo if not, mirroring DubbingDemo's missing-manifest behavior.
  useEffect(() => {
    let cancelled = false;
    apiFetch(`${API}${SCRIPTS[0].wav}`, { method: 'HEAD' })
      .then(() => {
        if (!cancelled) setAssetsAvailable(true);
      })
      .catch(() => {
        if (!cancelled) setAssetsAvailable(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!desktop) return;
    // `unregistered` is its own state, not a flavour of `unknown`: the OS
    // refused the accelerator, usually because another app already holds it
    // (the default collides with 1Password Quick Access on macOS). Saying
    // "no hotkey registered" there would read as "we have not checked yet",
    // when what the user needs to know is that this specific combination is
    // taken and they should pick another (#1858).
    setHotkeyState((current) => {
      if (shortcut.backend === 'unregistered') return 'unregistered';
      if (current === 'verified') return current;
      return shortcut.backend === 'focused' ? 'unknown' : 'registered';
    });
  }, [desktop, shortcut.backend]);

  // Subscribe to dictation events: the moment the user presses their
  // hotkey while this panel is mounted, flip to verified.
  useEffect(() => {
    if (!isTauri()) return;
    let unlistenStart, unlistenStop;
    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        unlistenStart = await listen('tray-dictate', () => {
          setHotkeyState('verified');
        });
        unlistenStop = await listen('tray-dictate-stop', () => {
          setHotkeyState('verified');
        });
      } catch {
        // Tauri event API unavailable — leave state alone.
      }
    })();
    return () => {
      try {
        unlistenStart && unlistenStart();
      } catch {
        /* noop */
      }
      try {
        unlistenStop && unlistenStop();
      } catch {
        /* noop */
      }
    };
  }, []);

  const togglePlay = (script) => {
    const audio = audioRef.current;
    if (!audio) return;
    if (playingId === script.id) {
      audio.pause();
      setPlayingId(null);
      return;
    }
    audio.src = `${API}${script.wav}`;
    audio.currentTime = 0;
    audio
      .play()
      .then(() => setPlayingId(script.id))
      .catch((e) => {
        console.warn('Sample playback failed:', e);
        setPlayingId(null);
      });
  };

  // Replay path: fetch the bundled WAV, post it to the transcribe endpoint,
  // render what the engine heard. Demonstrates the full dictation pipeline
  // without requiring mic permission or a hotkey press.
  const replay = async (script) => {
    setTranscripts((prev) => ({
      ...prev,
      [script.id]: { state: 'loading', text: '', error: '' },
    }));
    try {
      const wavRes = await apiFetch(`${API}${script.wav}`);
      const blob = await wavRes.blob();
      const fd = new FormData();
      fd.append('audio', blob, `${script.id}.wav`);
      const tRes = await apiFetch(`${API}/transcribe`, { method: 'POST', body: fd });
      const json = await tRes.json();
      setTranscripts((prev) => ({
        ...prev,
        [script.id]: { state: 'ok', text: json.text || '', error: '' },
      }));
    } catch (e) {
      // Typed 409 on a TTS-only install: no ASR model on disk. Render the
      // human message + the one-click download CTA instead of the raw
      // "409 Conflict: …" string.
      const missing = asrMissingPayload(e);
      if (missing) toastAsrModelMissing(missing);
      setTranscripts((prev) => ({
        ...prev,
        [script.id]: {
          state: 'fail',
          text: '',
          error: missing ? t('asr_missing.message') : e?.message || String(e),
        },
      }));
    }
  };

  const statusBadge = (() => {
    switch (hotkeyState) {
      case 'verified':
        return (
          <span
            className={`${STATUS_BASE} border-transparent bg-[rgba(152,151,26,0.12)] text-[#b8bb26]`}
          >
            <CheckCircle2 size={12} /> {t('demo.dictation_status_ok')}
          </span>
        );
      case 'unregistered':
        return (
          <span
            className={`${STATUS_BASE} border-transparent bg-[rgba(204,36,29,0.12)] text-[#fb4934]`}
          >
            <AlertTriangle size={12} />{' '}
            {t('demo.dictation_status_taken', {
              defaultValue:
                'Another app already uses this shortcut — pick a different one in Settings.',
            })}{' '}
            <code className="font-mono text-[10px] px-[4px] py-[1px] bg-[rgba(0,0,0,0.3)] rounded-[3px]">
              {shortcut.display}
            </code>
          </span>
        );
      case 'registered':
        return (
          <span
            className={`${STATUS_BASE} border-transparent bg-[rgba(215,153,33,0.10)] text-[#fabd2f]`}
          >
            <Keyboard size={12} /> {t('demo.dictation_status_pending')}{' '}
            <code className="font-mono text-[10px] px-[4px] py-[1px] bg-[rgba(0,0,0,0.3)] rounded-[3px]">
              {shortcut.display}
            </code>
          </span>
        );
      default:
        return (
          <span
            className={`${STATUS_BASE} border-transparent bg-[rgba(204,36,29,0.10)] text-[#fb4934]`}
          >
            <AlertTriangle size={12} /> {t('demo.dictation_status_warn')}
          </span>
        );
    }
  })();

  // The hotkey card always has something real to teach (the registered
  // shortcut + live press-to-verify) — only the replayable script cards
  // depend on the bundled WAVs, which installs don't always ship. Hiding
  // the whole panel left the wizard's "Try dictation" act completely
  // blank on every such install (#119/#124 follow-up, refined).
  // `checking` still shows the cards: the probe resolves in well under a
  // second and flashing the install panel first would be worse than a brief
  // wait. Only a confirmed-missing model swaps them out.
  const asrMissing = readiness.phase === 'missing';
  const showScripts = assetsAvailable !== false && !asrMissing;

  return (
    <section
      className={`dictation-demo flex flex-col gap-[10px] ${
        embedded
          ? 'mb-[12px]'
          : 'p-[14px] rounded-[10px] border border-border bg-[rgba(255,255,255,0.02)] mb-[16px]'
      }`}
    >
      <header className="flex items-center justify-between gap-[12px] flex-wrap">
        <h3 className="inline-flex items-center gap-[6px] m-0 text-[13px] font-bold text-fg">
          <Mic size={14} /> {t('demo.dictation_title')}
        </h3>
        {statusBadge}
      </header>

      <p className="m-0 text-[11px] leading-[1.45] text-fg-muted">
        {showScripts
          ? t('demo.dictation_lede')
          : t(
              'demo.dictation_lede_hotkey_only',
              'Hold the shortcut above anywhere on your desktop, speak, release — the text lands in whatever app has focus. Press it now to verify it works.',
            )}
      </p>

      <audio ref={audioRef} onEnded={() => setPlayingId(null)} preload="none" />

      {asrMissing && (
        <div className="flex flex-col gap-2 rounded-[8px] border border-border bg-[rgba(0,0,0,0.15)] px-[12px] py-[10px]">
          <p className="m-0 text-[11px] leading-[1.45] text-fg-muted">{t('asr_missing.message')}</p>
          <AsrModelChooser
            fallback={readiness.missing?.recommended}
            onInstall={readiness.install}
            onSelect={readiness.select}
            disabled={readiness.phase === 'installing'}
          />
        </div>
      )}

      {showScripts && (
        <div className="dictation-demo__scripts grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-[10px]">
          {SCRIPTS.map((s) => {
            const isPlaying = playingId === s.id;
            const tx = transcripts[s.id] || {};
            return (
              <div
                key={s.id}
                className="flex flex-col gap-[6px] px-[12px] py-[10px] rounded-[8px] border border-border bg-[rgba(0,0,0,0.15)]"
              >
                <div className="flex items-center gap-[8px] text-[10px] text-fg-muted">
                  <span className="font-mono text-[9px] px-[5px] py-[1px] rounded-sm bg-[rgba(255,255,255,0.06)] uppercase tracking-[0.04em]">
                    {s.language}
                  </span>
                  <span className="font-semibold text-[11px] text-fg normal-case">
                    {t(s.labelKey)}
                  </span>
                </div>
                <blockquote className="m-0 px-[8px] py-[6px] text-[11.5px] leading-[1.45] border-l-2 border-l-transparent bg-[rgba(255,255,255,0.02)] text-fg italic">
                  {s.text}
                </blockquote>
                <div className="flex gap-[6px] mt-[2px]">
                  <Button
                    size="sm"
                    variant="subtle"
                    onClick={() => togglePlay(s)}
                    leading={isPlaying ? <Pause size={11} /> : <Play size={11} />}
                    aria-label={
                      isPlaying
                        ? t('demo.aria_pause', { label: t(s.labelKey) })
                        : t('demo.aria_hear', { label: t(s.labelKey) })
                    }
                  >
                    {isPlaying ? t('demo.dictation_stop') : t('demo.dictation_hear')}
                  </Button>
                  <Button
                    size="sm"
                    variant="subtle"
                    onClick={() => replay(s)}
                    loading={tx.state === 'loading'}
                    leading={tx.state !== 'loading' && <Mic size={11} />}
                    aria-label={t('demo.aria_replay', { label: t(s.labelKey) })}
                  >
                    {tx.state === 'loading'
                      ? t('demo.dictation_transcribing')
                      : t('demo.dictation_replay')}
                  </Button>
                </div>
                {tx.state === 'ok' && (
                  <div className="flex items-start gap-[6px] text-[11px] px-[8px] py-[6px] rounded-lg leading-[1.4] text-[#b8bb26] bg-[rgba(152,151,26,0.08)] border border-transparent">
                    <CheckCircle2 size={11} /> <em className="not-italic">{tx.text}</em>
                  </div>
                )}
                {tx.state === 'fail' && (
                  <div className="flex items-start gap-[6px] text-[11px] px-[8px] py-[6px] rounded-lg leading-[1.4] text-[#fb4934] bg-[rgba(204,36,29,0.08)] border border-transparent">
                    <AlertTriangle size={11} /> {tx.error}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
