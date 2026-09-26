import { describe, it, expect, vi, beforeEach } from 'vitest';

import {
  scrubText,
  buildBugReportUrl,
  ISSUES_URL,
  REDACTED,
  clampCrashTail,
  rootCauseLine,
} from './bugReport';
import { getLastBackendCrash } from './backendCrash';

// #941: keep the real crashAge/describeCrashExit helpers; only the shell
// bridge is mocked (it resolves null by default, like a non-Tauri context —
// every pre-existing test keeps its no-crash behavior).
vi.mock('./backendCrash', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, getLastBackendCrash: vi.fn().mockResolvedValue(null) };
});

describe('scrubText — frontend twin of backend/core/scrub.py', () => {
  it.each([
    ['/Users/alice/Library/Logs/app.log', '~/Library/Logs/app.log'],
    ['/home/bob/.omnivoice/omnivoice.log', '~/.omnivoice/omnivoice.log'],
    ['C:\\Users\\carol\\AppData\\Roaming\\VoiceStudio', '~\\AppData\\Roaming\\VoiceStudio'],
    // Windows paths normalized to forward slashes (webview stacks, file URLs)
    ['C:/Users/dave/AppData/Local/OmniVoice/app.log', '~/AppData/Local/OmniVoice/app.log'],
    ['file:///C:/Users/erin/project/index.js', '~/project/index.js'],
  ])('redacts home path %s', (raw, expected) => {
    expect(scrubText(raw)).toBe(expected);
  });

  it.each([
    [`hf_${'A'.repeat(34)}`],
    [`ghp_${'B'.repeat(36)}`],
    [`github_pat_${'C'.repeat(22)}`],
    [`sk-${'d'.repeat(40)}`],
  ])('redacts credential-shaped %s', (secret) => {
    const out = scrubText(`auth failed: token=${secret}`);
    expect(out).not.toContain(secret);
    expect(out).toContain(REDACTED);
  });

  it.each([['hf_hub'], ['sk-learn'], ['ghp_x']])('leaves short identifier %s alone', (benign) => {
    expect(scrubText(`import error in ${benign}`)).toContain(benign);
  });

  it('handles null/undefined', () => {
    expect(scrubText(null)).toBe('');
    expect(scrubText(undefined)).toBe('');
  });

  // ── Hardening regressions (diagnostics audit) ──────────────────────────
  it.each([['c:\\users\\john\\log.txt'], ['C:\\Users\\john\\log.txt'], ['C:/USERS/john/log.txt']])(
    'redacts Windows home regardless of case: %s',
    (raw) => {
      expect(scrubText(raw)).not.toContain('john');
    },
  );

  // Built from low-entropy parts so they match the scrubber's shape without
  // tripping GitHub push-protection secret scanning.
  it.each([
    [`eyJ${'a'.repeat(20)}.${'b'.repeat(20)}.${'c'.repeat(20)}`], // JWT
    [`AIza${'B'.repeat(35)}`], // Google
    [`xox${'b-'}${'C'.repeat(20)}`], // Slack
    [`AKIA${'D'.repeat(16)}`], // AWS
  ])('redacts broadened credential shape %s', (secret) => {
    expect(scrubText(`error: ${secret}`)).not.toContain(secret);
  });

  it('redacts a URL query secret value but keeps the param name', () => {
    const out = scrubText('GET https://h/api?token=supersecretvalue12345&x=1');
    expect(out).not.toContain('supersecretvalue12345');
    expect(out).toContain('token=');
    expect(out).toContain('x=1');
  });
});

describe('buildBugReportUrl — encoded length ceiling', () => {
  beforeEach(() => {
    // Hermetic like the sibling describe: without this stub the builder
    // fetches real backend facts, and a dev machine with a wedged local
    // backend (port open, never answering) hangs this test past its timeout.
    // The subject here is encoding math, not reachability.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('ECONNREFUSED')));
  });

  it('keeps the ENCODED body under the ceiling even when the raw body is dense', async () => {
    // A body full of chars that expand under encodeURIComponent (newlines,
    // spaces, backticks) must still yield a URL comfortably under ~8k.
    const error = new Error('x'.repeat(200));
    error.stack = `${'trace line with spaces\n'.repeat(500)}`;
    const url = await buildBugReportUrl({ error });
    expect(url.length).toBeLessThan(8000);
  });
});

describe('buildBugReportUrl', () => {
  beforeEach(() => {
    // Backend down — the builder must still produce a usable URL.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('ECONNREFUSED')));
  });

  it('targets the issues/new endpoint with bug label', async () => {
    const url = await buildBugReportUrl();
    expect(url.startsWith(`${ISSUES_URL}?`)).toBe(true);
    expect(url).toContain(`labels=${encodeURIComponent('bug')}`);
  });

  it('embeds the scrubbed error message and stack', async () => {
    const err = new Error('cannot open /Users/alice/voice.wav');
    const url = await buildBugReportUrl({ error: err });
    const body = decodeURIComponent(url);
    expect(body).toContain('## Error');
    expect(body).toContain('cannot open ~/voice.wav');
    expect(body).not.toContain('/Users/alice');
  });

  it('records the backend error class so identical messages differ (#1800)', async () => {
    const err = new Error('Generation failed. Check the selected engine and try again.');
    err.errorClass = 'MemoryError';
    const body = decodeURIComponent(await buildBugReportUrl({ error: err }));
    expect(body).toContain('Backend error class: MemoryError');
  });

  it('omits the class line when the failure carries none', async () => {
    const body = decodeURIComponent(await buildBugReportUrl({ error: new Error('plain failure') }));
    expect(body).not.toContain('Backend error class');
  });

  it('seeds the title with the error message', async () => {
    const url = await buildBugReportUrl({ error: new Error('synthesis exploded') });
    expect(decodeURIComponent(url)).toContain('[Bug] synthesis exploded');
  });

  it('stays under the prefill URL ceiling on huge stacks', async () => {
    const err = new Error('boom');
    err.stack = 'at frame\n'.repeat(5000);
    const url = await buildBugReportUrl({ error: err });
    expect(url.length).toBeLessThan(8000);
  });
});

describe('buildBugReportUrl — crash-marker enrichment (#941)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('ECONNREFUSED')));
    getLastBackendCrash.mockResolvedValue(null);
  });

  it('attaches the crash evidence (exit code + scrubbed stderr tail)', async () => {
    getLastBackendCrash.mockResolvedValue({
      ts: Math.floor(Date.now() / 1000) - 30,
      exit_code: 3221226505,
      signal: null,
      exit_desc: 'exit code: 3221226505',
      backend_version: '0.3.10',
      uptime_s: 42,
      last_stderr:
        'File "/Users/alice/omnivoice/backend/main.py", line 1\nOSError: paging file too small',
      acknowledged: true, // acked markers still ride along — ack ≠ delete
    });
    const body = decodeURIComponent(await buildBugReportUrl());
    expect(body).toContain('## Last backend crash');
    expect(body).toContain('exit code 3221226505');
    expect(body).toContain('**Uptime before crash:** 42 s');
    // Home paths in the stderr tail are scrubbed like every other section.
    expect(body).toContain('~/omnivoice/backend/main.py');
    expect(body).not.toContain('/Users/alice');
  });

  it('omits the section entirely when no crash was ever recorded', async () => {
    const body = decodeURIComponent(await buildBugReportUrl());
    expect(body).not.toContain('## Last backend crash');
  });

  it('keeps the newest end of an oversized stderr tail and stays under the URL ceiling', async () => {
    getLastBackendCrash.mockResolvedValue({
      ts: Math.floor(Date.now() / 1000),
      exit_code: 1,
      signal: null,
      exit_desc: 'exit status: 1',
      backend_version: '0.3.10',
      uptime_s: 1,
      last_stderr: `${'boot noise line\n'.repeat(400)}THE REAL TRACEBACK LINE`,
      acknowledged: false,
    });
    const url = await buildBugReportUrl();
    const body = decodeURIComponent(url);
    expect(body).toContain('THE REAL TRACEBACK LINE'); // tail kept, head dropped
    expect(url.length).toBeLessThan(8000);
  });

  it('rescues the root cause of a chained traceback from the dropped head (#1376)', async () => {
    // The #1376 shape: transformers' lazy-import wrapper is what survives at
    // the END of stderr, and it says nothing. The real cause is at the TOP,
    // which is exactly what a tail-only clamp discards — so the report arrived
    // with the useless half and triage had to guess the useful one.
    const stderr = [
      'Traceback (most recent call last):',
      '  File "transformers/utils/import_utils.py", line 2184, in __getattr__',
      'ImportError: operator torchvision::nms does not exist',
      '',
      'The above exception was the direct cause of the following exception:',
      '',
      `${'  frame noise\n'.repeat(400)}`,
      "ModuleNotFoundError: Could not import module 'GenerationMixin'.",
    ].join('\n');
    getLastBackendCrash.mockResolvedValue({
      ts: Math.floor(Date.now() / 1000),
      exit_code: 1,
      signal: null,
      exit_desc: 'exit status: 1',
      backend_version: '0.4.2',
      uptime_s: 28,
      last_stderr: stderr,
      acknowledged: false,
    });
    const url = await buildBugReportUrl();
    const body = decodeURIComponent(url);
    expect(body).toContain('operator torchvision::nms does not exist');
    expect(body).toContain('the line above is the original cause');
    expect(body).toContain("Could not import module 'GenerationMixin'"); // tail still kept
    expect(url.length).toBeLessThan(8000);
  });
});

describe('clampCrashTail / rootCauseLine (#1376)', () => {
  it('leaves a tail that fits completely alone', () => {
    expect(clampCrashTail('short traceback', 100)).toBe('short traceback');
  });

  it('falls back to plain tail-keeping when there is no chain', () => {
    const out = clampCrashTail(`${'x'.repeat(500)}\nRuntimeError: boom`, 40);
    expect(out.startsWith('… (truncated)')).toBe(true);
    expect(out).toContain('RuntimeError: boom');
  });

  it('does not repeat a root cause that survived inside the kept tail', () => {
    // Boot noise first, so a budget exists that keeps the root cause inside the
    // tail — that is the case where prepending it would duplicate the line.
    const text =
      'boot\n'.repeat(200) +
      'ValueError: the original\n\n' +
      'The above exception was the direct cause of the following exception:\n\n' +
      '  frame\n'.repeat(40) +
      'RuntimeError: the wrapper';
    const out = clampCrashTail(text, 600);
    expect(out).toContain('ValueError: the original');
    expect(out.match(/ValueError: the original/g)).toHaveLength(1);
  });

  it('never returns more than the budget, chained or not', () => {
    const chained = [
      'ImportError: a fairly long original cause line to eat into the budget',
      '',
      'During handling of the above exception, another exception occurred:',
      '',
      `${'  frame line\n'.repeat(500)}`,
      'RuntimeError: wrapper',
    ].join('\n');
    for (const max of [300, 600, 1200]) {
      expect(clampCrashTail(chained, max).length).toBeLessThanOrEqual(max);
      expect(clampCrashTail('x'.repeat(9000), max).length).toBeLessThanOrEqual(max);
    }
  });

  it('ignores a marker that is only QUOTED inside an error message', () => {
    // stderr routinely quotes tracebacks (a logged exception, a subprocess's
    // captured output). A substring match would treat the quotation as a real
    // chain and attribute a root cause from the wrong exception.
    const text = [
      `${'noise\n'.repeat(300)}`,
      'RuntimeError: parser saw "The above exception was the direct cause of the following exception" in the input',
    ].join('\n');
    expect(rootCauseLine(text)).toBe('');
    expect(clampCrashTail(text, 400).startsWith('… (truncated)')).toBe(true);
  });

  it('picks the exception line, not an indented frame', () => {
    const text = [
      'Traceback (most recent call last):',
      '  File "a.py", line 1, in <module>',
      '    import thing',
      'ImportError: no thing',
      '',
      'During handling of the above exception, another exception occurred:',
      '',
      'RuntimeError: wrapper',
    ].join('\n');
    expect(rootCauseLine(text)).toBe('ImportError: no thing');
  });

  it('returns nothing for an unchained traceback', () => {
    expect(rootCauseLine('Traceback…\nValueError: solo')).toBe('');
  });
});

describe('buildBugReportUrl — backend reachability section (#1164)', () => {
  beforeEach(async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('ECONNREFUSED')));
    getLastBackendCrash.mockResolvedValue(null);
    const { _resetBackendContactForTests } = await import('./backendContact');
    _resetBackendContactForTests();
  });

  it('reports deployment mode and "never answered" when there was no contact', async () => {
    const body = decodeURIComponent(await buildBugReportUrl());
    expect(body).toContain('## Backend reachability');
    expect(body).toContain('**Deployment mode:** `dev`'); // vitest = DEV, no Tauri
    expect(body).toContain('none this session');
  });

  it('reports the cached last contact even while the backend is down', async () => {
    const { recordBackendContact } = await import('./backendContact');
    recordBackendContact(Date.now() - 4000);
    const body = decodeURIComponent(await buildBugReportUrl());
    expect(body).toMatch(/\*\*Last backend response:\*\* \d+ s before this report/);
  });

  it('includes the transport ApiError diagnostics when the report is built from one', async () => {
    const err = new Error("Can't reach the local VoiceStudio backend");
    err.detail = {
      transport: 'Failed to fetch /Users/alice/x',
      mode: 'server',
      lastContactMs: null,
      firstFailureTs: 1_700_000_000_000,
      attempts: 4,
    };
    const body = decodeURIComponent(await buildBugReportUrl({ error: err }));
    expect(body).toContain('**Attempts before giving up:** 4');
    expect(body).toContain('**Mode at failure time:** `server`');
    expect(body).toContain('**First failure:** 2023-11-14');
    // Scrubbed like every other section.
    expect(body).toContain('~/x');
    expect(body).not.toContain('/Users/alice');
  });

  it('stays under the URL ceiling with the extra section', async () => {
    const err = new Error('boom');
    err.stack = 'at frame\n'.repeat(5000);
    err.detail = { transport: 'x'.repeat(5000), mode: 'dev', attempts: 4 };
    const url = await buildBugReportUrl({ error: err });
    expect(url.length).toBeLessThan(8000);
  });
});

describe('buildIssueSearchUrl', () => {
  it('builds a scrubbed, noise-free search query', async () => {
    const { buildIssueSearchUrl } = await import('./bugReport');
    const url = buildIssueSearchUrl(
      new Error('CUDA error 700 at /home/eve/cache: illegal memory access'),
    );
    const q = decodeURIComponent(url.split('q=')[1]);
    expect(url).toContain('github.com/debpalash/VoiceStudio/issues?q=');
    expect(q).toContain('CUDA error');
    expect(q).not.toContain('700'); // machine-specific noise stripped
    expect(q).not.toContain('/home/eve'); // scrubbed + punctuation-stripped
  });

  it('survives an empty error', async () => {
    const { buildIssueSearchUrl } = await import('./bugReport');
    expect(buildIssueSearchUrl(null)).toContain('issues?q=');
  });
});
