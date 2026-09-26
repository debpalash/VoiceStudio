import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import BackendCrashNotice from './BackendCrashNotice';
import { acknowledgeBackendCrash, getUnacknowledgedBackendCrash } from '../utils/backendCrash';

// #941: the crash-notice branch — a recorded backend death must surface the
// honest message (exit code + age) with a "View crash details" affordance,
// and viewing/dismissing must acknowledge the marker.
vi.mock('../utils/backendCrash', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    getUnacknowledgedBackendCrash: vi.fn().mockResolvedValue(null),
    acknowledgeBackendCrash: vi.fn().mockResolvedValue(undefined),
  };
});
vi.mock('../utils/bugReport', () => ({
  openBugReport: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('../api/external', () => ({
  openExternal: vi.fn().mockResolvedValue(undefined),
}));

const MARKER = {
  ts: Math.floor(Date.now() / 1000) - 12,
  exit_code: 134,
  signal: null,
  exit_desc: 'exit status: 134',
  backend_version: '0.3.10',
  uptime_s: 87,
  last_stderr: 'CUDA error: an illegal memory access was encountered',
  acknowledged: false,
};

describe('BackendCrashNotice', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getUnacknowledgedBackendCrash.mockResolvedValue(null);
  });

  it('renders nothing when the shell reports no crash', async () => {
    const { container } = render(<BackendCrashNotice />);
    await waitFor(() => expect(getUnacknowledgedBackendCrash).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the honest message and the details affordance for a fresh marker', async () => {
    getUnacknowledgedBackendCrash.mockResolvedValue(MARKER);
    render(<BackendCrashNotice />);
    const alert = await screen.findByRole('alert');
    // Honest: names the exit code instead of a vague "can't reach".
    expect(alert.textContent).toContain('crashed');
    expect(alert.textContent).toContain('exit code 134');
    expect(screen.getByRole('button', { name: /view crash details/i })).toBeInTheDocument();
  });

  it('surfaces a crash pushed via the ov:backend-crashed event', async () => {
    render(<BackendCrashNotice />);
    await waitFor(() => expect(getUnacknowledgedBackendCrash).toHaveBeenCalled());
    window.dispatchEvent(new CustomEvent('ov:backend-crashed', { detail: MARKER }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('exit code 134');
  });

  it('acks on view and shows the stderr tail in the details dialog', async () => {
    getUnacknowledgedBackendCrash.mockResolvedValue(MARKER);
    render(<BackendCrashNotice />);
    fireEvent.click(await screen.findByRole('button', { name: /view crash details/i }));
    expect(acknowledgeBackendCrash).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/illegal memory access/)).toBeInTheDocument();
    expect(screen.getByText('Backend crash details')).toBeInTheDocument();
  });

  it('ack + clear on dismiss', async () => {
    getUnacknowledgedBackendCrash.mockResolvedValue(MARKER);
    render(<BackendCrashNotice />);
    await screen.findByRole('alert');
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(acknowledgeBackendCrash).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });
});

// #1375: the run sentinel does not know whether the previous run crashed —
// sleep, force-quit, a stopped VM and a Docker restart leave the same trace —
// and a sentinel with no captured output produces a bug report whose evidence
// block is EMPTY: filed in good faith, unanswerable, left open. The notice
// stays; the crash framing and the one-click report are what get gated.
describe('BackendCrashNotice — sentinel evidence gate (#1375)', () => {
  const SENTINEL_NO_EVIDENCE = {
    ts: Math.floor(Date.now() / 1000) - 120,
    exit_code: null,
    signal: null,
    exit_desc: 'process ended uncleanly (previous run)',
    backend_version: '0.4.2',
    uptime_s: 0,
    last_stderr: '',
    acknowledged: false,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('an evidence-free sentinel is worded as unclean, not crashed, and offers no one-click report', async () => {
    getUnacknowledgedBackendCrash.mockResolvedValue(SENTINEL_NO_EVIDENCE);
    render(<BackendCrashNotice />);
    // Unclean wording, naming the benign causes.
    const banner = await screen.findByRole('alert');
    expect(banner.textContent).toMatch(/without a clean shutdown/);
    expect(banner.textContent).not.toMatch(/crashed/);

    fireEvent.click(screen.getByText('View crash details'));
    // The dialog explains the ambiguity...
    expect(
      await screen.findByText(/cannot tell whether it crashed or was simply interrupted/),
    ).toBeInTheDocument();
    // ...says why there is no report button...
    expect(screen.getByText(/one-click report would be empty/)).toBeInTheDocument();
    // ...and the Report button is genuinely absent.
    expect(screen.queryByText('Report this bug')).not.toBeInTheDocument();
  });

  it('a sentinel WITH a log tail keeps the one-click report — under an honest title', async () => {
    getUnacknowledgedBackendCrash.mockResolvedValue({
      ...SENTINEL_NO_EVIDENCE,
      uptime_s: 44,
      last_stderr: 'last activity before the death: generate\nTraceback: boom',
    });
    render(<BackendCrashNotice />);
    fireEvent.click(await screen.findByText('View crash details'));
    // Still worded as unclean (the sentinel still cannot know)...
    expect(
      await screen.findByText(/cannot tell whether it crashed or was simply interrupted/),
    ).toBeInTheDocument();
    // ...but there is evidence, so reporting is one click again.
    fireEvent.click(screen.getByText('Report this bug'));
    // The report's TITLE must not claim a death the sentinel cannot attest to
    // — "Backend died (process ended uncleanly …)" states as fact what the
    // marker only suspects.
    const { openBugReport } = await import('../utils/bugReport');
    await waitFor(() => expect(openBugReport).toHaveBeenCalled());
    const { title } = openBugReport.mock.calls[0][0];
    expect(title).toMatch(/ended uncleanly/);
    expect(title).not.toMatch(/died/);
  });

  it('a real crash with an exit code is unchanged: crash wording and the report button', async () => {
    getUnacknowledgedBackendCrash.mockResolvedValue({ ...MARKER, last_stderr: '' });
    render(<BackendCrashNotice />);
    const banner = await screen.findByRole('alert');
    expect(banner.textContent).toMatch(/crashed/);
    fireEvent.click(screen.getByText('View crash details'));
    // An exit code IS evidence (a native fault can die before logging a byte),
    // so the report path stays even with an empty tail.
    expect(await screen.findByText('Report this bug')).toBeInTheDocument();
  });

  // #1927: the crash details dialog named the exit code and dumped the log,
  // and stopped there. A Windows access violation reads as
  // "exit code -1073741819" — a number that means nothing to the person it is
  // shown to — and the report filed against it had no cause and no next step.
  // The classifier already knew what it meant; nothing rendered it outside a
  // dropped stream.
  it('explains what the exit code means and what to try', async () => {
    getUnacknowledgedBackendCrash.mockResolvedValue({
      ...MARKER,
      exit_code: -1073741819, // STATUS_ACCESS_VIOLATION
      last_stderr: 'INFO [omnivoice.model] Loading VoiceStudio model on device: cuda',
    });
    render(<BackendCrashNotice />);
    fireEvent.click(await screen.findByRole('button', { name: /view crash details/i }));

    const dialog = await screen.findByRole('dialog');
    // A native fault is not a memory problem, and the guidance says so.
    expect(dialog.textContent).toMatch(/compute stack/i);
    expect(dialog.textContent).toMatch(/GPU driver/i);
  });

  it('gives a port conflict its own explanation, not the GPU one', async () => {
    getUnacknowledgedBackendCrash.mockResolvedValue({
      ...MARKER,
      exit_code: 78, // EX_CONFIG — the port was already held
      last_stderr: '',
    });
    render(<BackendCrashNotice />);
    fireEvent.click(await screen.findByRole('button', { name: /view crash details/i }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).toMatch(/already in use/i);
    expect(dialog.textContent).not.toMatch(/GPU driver/i);
  });

  it('offers no cause for a sentinel marker, which cannot know there was one', async () => {
    getUnacknowledgedBackendCrash.mockResolvedValue({
      ...MARKER,
      exit_code: null,
      signal: null,
      exit_desc: 'process ended uncleanly (previous run)',
      uptime_s: 0,
      last_stderr: '',
    });
    render(<BackendCrashNotice />);
    fireEvent.click(await screen.findByRole('button', { name: /view crash details/i }));

    const dialog = await screen.findByRole('dialog');
    // Sleep, force-quit and a stopped VM leave this same trace. Explaining a
    // crash that may not have happened is the #1375 fabrication.
    expect(dialog.textContent).not.toMatch(/VRAM|GPU driver|compute stack/i);
  });
});
