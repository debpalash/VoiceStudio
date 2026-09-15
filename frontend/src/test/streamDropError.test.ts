import { describe, it, expect, vi, afterEach } from 'vitest';
import { streamDropError } from '../utils/backendCrash';

// #1062: a dub transcribe stream that closed with NO terminal event surfaced
// "Likely ASR backend failed to load" — a GUESS. The backend is contract-bound
// to emit a terminal event on every stream (test_transcribe_stream_never_closes
// _without_terminal_event), so a silent drop means the backend PROCESS died —
// on small-VRAM GPUs, a native OOM abort while loading ASR. When the shell
// recorded a crash marker (#941), the error must tell that story instead.

const FALLBACK = 'Transcribe stream dropped before emitting any segments.';

// `streamDropError`'s no-marker branch asks whether the backend is still
// answering (#1242): a live process rules the caller's "it crashed" guess out.
// Its default probe is a REAL `fetch` at the configured API origin, so a test
// that leaves it unstubbed asserts against whatever happens to be listening on
// the developer's machine — three of the tests below passed in CI (nothing
// there) and failed for anyone running the app locally. Every test states which
// answer it wants.
const DEAD = async () => false;
const ALIVE = async () => true;

function marker(overrides = {}) {
  return {
    ts: Math.floor(Date.now() / 1000) - 12,
    exit_code: null,
    signal: 6,
    exit_desc: 'signal: 6',
    backend_version: '0.3.19',
    uptime_s: 90,
    last_stderr: 'RuntimeError: CUDA out of memory',
    acknowledged: false,
    ...overrides,
  };
}

describe('streamDropError (#1062)', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('tells the honest crash story (not a guess) when a crash marker exists', async () => {
    const events: unknown[] = [];
    const onCrash = (e: Event) => events.push((e as CustomEvent).detail);
    window.addEventListener('ov:backend-crashed', onCrash);

    const err = await streamDropError(FALLBACK, async () => marker() as never);

    expect(err.message).toContain('backend crashed');
    expect(err.message).toContain('signal 6');
    // Names the real next step for the common cause, instead of "check the log".
    expect(err.message).toMatch(/VRAM/i);
    expect(err.message).not.toContain(FALLBACK);
    // Raises the crash notice so "View crash details" is one click away.
    expect(events).toHaveLength(1);
    window.removeEventListener('ov:backend-crashed', onCrash);
  });

  it('keeps the caller message when there is no crash marker', async () => {
    const err = await streamDropError(FALLBACK, async () => null, { probeAlive: DEAD });
    expect(err.message).toBe(FALLBACK);
  });

  it('says a live backend was not the crash it looked like (#1242)', async () => {
    const err = await streamDropError(FALLBACK, async () => null, {
      probeAlive: ALIVE,
      mode: 'server',
    });
    // The caller's guess is dropped: the process answered, so it did not die.
    expect(err.message).not.toContain(FALLBACK);
    expect(err.message).toMatch(/still running/i);
    expect(err.message).toMatch(/proxy|buffering/i);
  });

  it('never masks the caller message when the forensics lookup itself fails', async () => {
    const err = await streamDropError(FALLBACK, async () => {
      throw new Error('no shell');
    });
    expect(err.message).toBe(FALLBACK);
  });
});

// #1119 — reported on v0.3.21, which already HAD the #1098 fix. The user still
// got the guess ("Likely ASR backend failed to load") because streamDropError
// asked for the crash marker ONCE, instantly, at the moment the stream dropped —
// racing the shell's ~2 s detect-and-write and losing. Same race #1102 fixed for
// apiFetch; this path never got it.
describe('streamDropError — waits for the shell to notice the death (#1119)', () => {
  it('finds a marker that arrives LATE instead of falling back to the guess', async () => {
    let calls = 0;
    const getCrash = async () => {
      calls += 1;
      // The supervisor hasn't noticed yet on the first two polls.
      return calls < 3 ? null : (marker() as never);
    };
    // Force the Tauri path so the wait loop engages, and make sleep instant.
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    try {
      const err = await streamDropError(FALLBACK, getCrash, {
        waitMs: 8000,
        intervalMs: 1,
        sleep: async () => {},
      });
      expect(err.message).toContain('backend crashed');
      expect(err.message).not.toContain(FALLBACK);
      expect(calls).toBeGreaterThanOrEqual(3);
    } finally {
      delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    }
  });

  it('still gives up and uses the caller message when no crash ever appears', async () => {
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    try {
      const err = await streamDropError(FALLBACK, async () => null, {
        waitMs: 5,
        intervalMs: 1,
        sleep: async () => {},
        probeAlive: DEAD,
      });
      expect(err.message).toBe(FALLBACK);
    } finally {
      delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    }
  });

  it('does not stall a browser/Docker user — no shell means no marker, ever', async () => {
    let calls = 0;
    const err = await streamDropError(
      FALLBACK,
      async () => {
        calls += 1;
        return null;
      },
      { probeAlive: DEAD },
    );
    expect(err.message).toBe(FALLBACK);
    expect(calls).toBe(1); // asked once, then stopped — no 8 s wait
  });
});

describe('crashCauseHint', () => {
  it('attributes SIGKILL to system memory, not VRAM', async () => {
    const { crashCauseHint } = await import('../utils/backendCrash');
    const hint = crashCauseHint({ exit_code: null, signal: 9 });
    expect(hint).toMatch(/memory \(RAM\)/);
    expect(hint).not.toMatch(/VRAM/);
  });

  it('keeps the VRAM guidance for real GPU aborts', async () => {
    const { crashCauseHint } = await import('../utils/backendCrash');
    expect(crashCauseHint({ exit_code: 1, signal: null })).toMatch(/VRAM/);
  });

  // #1275 (Windows 0xC0000005 on an RTX 2080 SUPER) and #1293 (SIGSEGV on
  // Linux) both fell through to the VRAM advice, which sent the user off to
  // flush a model that had nothing to do with a native fault.
  it('does not blame VRAM for a Windows access violation', async () => {
    const { crashCauseHint } = await import('../utils/backendCrash');
    const hint = crashCauseHint({ exit_code: -1073741819, signal: null });
    expect(hint).toMatch(/driver|downloaded incompletely/);
    expect(hint).not.toMatch(/VRAM/);
  });

  it('does not blame VRAM for a segfault', async () => {
    const { crashCauseHint } = await import('../utils/backendCrash');
    const hint = crashCauseHint({ exit_code: null, signal: 11 });
    expect(hint).toMatch(/driver|downloaded incompletely/);
    expect(hint).not.toMatch(/VRAM/);
  });

  // Names BOTH isolated engines: the crash marker records how the process
  // died, not which subsystem was running, so a segfault during transcription
  // looks identical to one during synthesis. Offering only the TTS engine sent
  // ASR crashes to a fix that leaves the crashing path untouched (Greptile P1).
  it('offers the isolated engine for synthesis AND for transcription', async () => {
    const { crashCauseHint } = await import('../utils/backendCrash');
    const hint = crashCauseHint({ exit_code: null, signal: 11 });
    expect(hint).toMatch(/VoiceStudio \(subprocess\)/);
    expect(hint).toMatch(/Faster-Whisper/);
  });

  it('still treats SIGKILL as memory pressure, not a native fault', async () => {
    const { crashCauseHint, isNativeFault } = await import('../utils/backendCrash');
    expect(isNativeFault({ exit_code: null, signal: 9 })).toBe(false);
    expect(crashCauseHint({ exit_code: null, signal: 9 })).toMatch(/memory \(RAM\)/);
  });

  it('leaves SIGABRT on the VRAM path — that is how a fatal CUDA error exits', async () => {
    const { crashCauseHint, isNativeFault } = await import('../utils/backendCrash');
    expect(isNativeFault({ exit_code: null, signal: 6 })).toBe(false);
    expect(crashCauseHint({ exit_code: null, signal: 6 })).toMatch(/VRAM/);
  });

  it('leaves the port-in-use exit alone', async () => {
    const { crashCauseHint, isNativeFault } = await import('../utils/backendCrash');
    expect(isNativeFault({ exit_code: 78, signal: null })).toBe(false);
    expect(crashCauseHint({ exit_code: 78, signal: null })).toMatch(/port 3900/);
  });

  // #1282 (`import torchaudio` exploding 4 s after launch) and #1376
  // (transformers' lazy loader raising ModuleNotFoundError 28 s in) both
  // arrived as a plain `exit code 1` and therefore both got the VRAM default —
  // telling users whose Python environment was half-installed to go flush a
  // TTS model. Nothing in the exit code distinguishes them; the traceback was
  // sitting unread in the marker.
  describe('a backend that died importing its own dependencies (#1282)', () => {
    const TORCHAUDIO_TAIL = [
      'Traceback (most recent call last):',
      '  File "...\\backend\\main.py", line 197, in <module>',
      '    import torchaudio',
      '  File "...\\torchaudio\\_internal\\__init__.py", line 4, in <module>',
      '    from torch.hub import download_url_to_file',
      "ImportError: cannot import name 'download_url_to_file'",
    ].join('\n');

    const TRANSFORMERS_TAIL =
      '  File "...\\transformers\\utils\\import_utils.py", line 2184, in __getattr__\n' +
      '    raise ModuleNotFoundError(\n' +
      "ModuleNotFoundError: Could not import module 'GenerationMixin'.";

    it('names the environment, not VRAM, for a torchaudio import failure', async () => {
      const { crashCauseHint } = await import('../utils/backendCrash');
      const hint = crashCauseHint({
        exit_code: 1,
        signal: null,
        last_stderr: TORCHAUDIO_TAIL,
      });
      expect(hint).toMatch(/Clean & Retry/);
      expect(hint).not.toMatch(/VRAM/);
    });

    it('catches the transformers lazy-import failure too', async () => {
      const { crashCauseHint } = await import('../utils/backendCrash');
      const hint = crashCauseHint({
        exit_code: 1,
        signal: null,
        last_stderr: TRANSFORMERS_TAIL,
      });
      expect(hint).toMatch(/Clean & Retry/);
      expect(hint).not.toMatch(/VRAM/);
    });

    it('wins over the native-fault branch — a DLL that will not load is still the environment', async () => {
      // On Windows a missing dependent DLL surfaces as an access violation.
      // "Update your GPU driver" is the wrong advice when the venv is broken.
      const { crashCauseHint } = await import('../utils/backendCrash');
      const hint = crashCauseHint({
        exit_code: -1073741819,
        signal: null,
        last_stderr: 'ImportError: DLL load failed while importing torch._C',
      });
      expect(hint).toMatch(/Clean & Retry/);
    });

    it('does not fire on an unrelated traceback that merely mentions torch', async () => {
      // Narrow on purpose: an ImportError is required, not just the word.
      const { crashCauseHint, isBrokenEnvironmentCrash } = await import('../utils/backendCrash');
      const marker = {
        exit_code: 1,
        signal: null,
        last_stderr: 'RuntimeError: CUDA error: out of memory (torch/cuda/__init__.py)',
      };
      expect(isBrokenEnvironmentCrash(marker)).toBe(false);
      expect(crashCauseHint(marker)).toMatch(/VRAM/);
    });

    it('does not fire on an import error in something optional', async () => {
      const { isBrokenEnvironmentCrash } = await import('../utils/backendCrash');
      expect(
        isBrokenEnvironmentCrash({
          last_stderr: "ModuleNotFoundError: No module named 'some_optional_plugin'",
        }),
      ).toBe(false);
    });

    it('never outranks an OOM kill, whose signal is a fact about THIS process', async () => {
      // backend_err.log is appended across runs and the shell captures its
      // last ~40 lines, so a process killed early carries the PREVIOUS run's
      // output. A stale import traceback must not turn a memory kill into
      // "rebuild your environment" (greptile).
      const { crashCauseHint } = await import('../utils/backendCrash');
      const hint = crashCauseHint({
        exit_code: null,
        signal: 9,
        last_stderr: TORCHAUDIO_TAIL,
      });
      expect(hint).toMatch(/memory \(RAM\)/);
      expect(hint).not.toMatch(/Clean & Retry/);
    });

    it('ignores an import traceback stranded above this run’s own output', async () => {
      const { isBrokenEnvironmentCrash } = await import('../utils/backendCrash');
      const stale = [
        TORCHAUDIO_TAIL,
        ...Array.from({ length: 25 }, (_, i) => `INFO  this run line ${i}`),
      ].join('\n');
      expect(isBrokenEnvironmentCrash({ last_stderr: stale })).toBe(false);
      // ...but the same traceback as the LAST thing written still counts.
      expect(isBrokenEnvironmentCrash({ last_stderr: TORCHAUDIO_TAIL })).toBe(true);
    });

    it('falls back cleanly when there is no captured tail at all', async () => {
      const { crashCauseHint, isBrokenEnvironmentCrash } = await import('../utils/backendCrash');
      expect(isBrokenEnvironmentCrash({ last_stderr: '' })).toBe(false);
      expect(crashCauseHint({ exit_code: 1, signal: null })).toMatch(/VRAM/);
    });
  });
});

describe('stream drop with no crash marker (#1242)', () => {
  const FB = 'fallback message with no cause asserted';

  // The reporter was in `server` mode with the backend having answered 20 s
  // earlier. No crash marker existed — correctly, since nothing had crashed —
  // and the caller's fallback then asserted "Likely ASR backend failed to
  // load" about a model that had loaded fine.
  it('does not blame the ASR model when the backend is still answering', async () => {
    const { streamDropError } = await import('../utils/backendCrash');
    const err = await streamDropError(FB, async () => null, {
      probeAlive: async () => true,
      waitMs: 0,
    });
    expect(err.message).not.toBe(FB);
    expect(err.message).toMatch(/still running|did not crash/i);
    expect(err.message).not.toMatch(/ASR/i);
  });

  it('names the proxy as the likely cause in a served deployment', async () => {
    const { streamDropError } = await import('../utils/backendCrash');
    const err = await streamDropError(FB, async () => null, {
      probeAlive: async () => true,
      waitMs: 0,
      mode: 'server',
    });
    expect(err.message).toMatch(/proxy|buffering/i);
  });

  it("keeps the caller's message when the backend is gone too", async () => {
    const { streamDropError } = await import('../utils/backendCrash');
    const err = await streamDropError(FB, async () => null, {
      probeAlive: async () => false,
      waitMs: 0,
    });
    expect(err.message).toBe(FB);
  });

  it('a real crash marker still wins over the liveness probe', async () => {
    const { streamDropError } = await import('../utils/backendCrash');
    const marker = {
      ts: Math.floor(Date.now() / 1000) - 5,
      exit_code: null,
      signal: 9,
      exit_desc: 'signal: 9',
      backend_version: '0.4.2',
      uptime_s: 30,
      last_stderr: '',
      acknowledged: false,
    };
    const err = await streamDropError(FB, async () => marker, {
      probeAlive: async () => true,
      waitMs: 0,
    });
    expect(err.message).toMatch(/memory \(RAM\)/);
  });
});

// #2108 — a desktop (Tauri) user got the reverse-proxy diagnosis for a dub
// transcribe stream that died on 127.0.0.1, where no proxy can exist. The
// backend had gone byte-silent for minutes refining voice references, the
// webview severed the idle connection, and the job itself finished fine.
describe('stream drop with the backend alive, by deployment mode (#2108)', () => {
  const FB = 'fallback with no cause asserted';
  const alive = { probeAlive: async () => true, waitMs: 0 };

  it('does not blame a proxy in the desktop shell — there is none to blame', async () => {
    const err = await streamDropError(FB, async () => null, { ...alive, mode: 'desktop' });
    expect(err.message).toMatch(/still running/i);
    expect(err.message).not.toMatch(/proxy|buffering|nginx/i);
    // Says where the evidence is, and that the job usually went on without the UI.
    expect(err.message).toMatch(/Logs/);
    expect(err.message).toMatch(/finishes/);
  });

  it('treats the dev server the same — it also talks to 127.0.0.1 directly', async () => {
    const err = await streamDropError(FB, async () => null, { ...alive, mode: 'dev' });
    expect(err.message).not.toMatch(/proxy|buffering/i);
  });

  it('detects the mode itself when the caller does not say (vitest runs as dev)', async () => {
    const err = await streamDropError(FB, async () => null, alive);
    expect(err.message).not.toMatch(/proxy|buffering/i);
  });

  it('keeps the proxy diagnosis for a served deployment', async () => {
    const err = await streamDropError(FB, async () => null, { ...alive, mode: 'server' });
    expect(err.message).toMatch(/proxy|buffering/i);
  });
});
