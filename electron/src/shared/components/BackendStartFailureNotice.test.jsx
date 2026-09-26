import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import BackendStartFailureNotice from './BackendStartFailureNotice';
import { openBugReport } from '../utils/bugReport';
import toast from 'react-hot-toast';

// #1177: the shell's `Failed { message }` diagnosis must reach the user AFTER
// the bootstrap splash is gone — the window in which a start failure used to
// collapse into the evidence-free "Can't reach the local VoiceStudio backend".
vi.mock('../utils/bugReport', () => ({
  openBugReport: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('../api/external', () => ({
  openExternal: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

const DIAGNOSIS =
  'Backend process exited (exit status: 1):\nModuleNotFoundError: No module named `torch`';

const emit = (message) =>
  window.dispatchEvent(new CustomEvent('ov:backend-start-failed', { detail: { message } }));

describe('BackendStartFailureNotice', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders nothing until the shell reports a start failure', () => {
    const { container } = render(<BackendStartFailureNotice />);
    expect(container).toBeEmptyDOMElement();
  });

  it('raises an alert with a way to see the diagnosis', async () => {
    render(<BackendStartFailureNotice />);
    emit(DIAGNOSIS);
    const alert = await screen.findByRole('alert');
    // Names what actually happened rather than "it may still be starting up".
    expect(alert.textContent).toMatch(/couldn't start/i);
    expect(screen.getByRole('button', { name: /see why/i })).toBeInTheDocument();
  });

  it('shows the shell output and an actionable hint in the details dialog', async () => {
    render(<BackendStartFailureNotice />);
    emit(DIAGNOSIS);
    fireEvent.click(await screen.findByRole('button', { name: /see why/i }));

    const dialog = await screen.findByRole('dialog');
    // The evidence itself — exit code + stderr tail — which is the whole point.
    expect(dialog.textContent).toContain('exit status: 1');
    expect(dialog.textContent).toContain('ModuleNotFoundError');
    // …plus the same actionable next step the splash offers for this shape.
    expect(dialog.textContent).toMatch(/Clean & Retry/i);
  });

  // #1112's dead end: an Intel Mac can never succeed, so no Retry advice.
  it('omits the retry hint for a failure retrying can never fix', async () => {
    render(<BackendStartFailureNotice />);
    emit("Intel Macs can't run the local AI backend (PyTorch ships no x86_64 wheels).");
    fireEvent.click(await screen.findByRole('button', { name: /see why/i }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).not.toMatch(/Clean & Retry/i);
  });

  it('reports the diagnosis through the bug-report affordance', async () => {
    render(<BackendStartFailureNotice />);
    emit(DIAGNOSIS);
    fireEvent.click(await screen.findByRole('button', { name: /see why/i }));
    fireEvent.click(await screen.findByRole('button', { name: /report/i }));

    await waitFor(() => expect(openBugReport).toHaveBeenCalled());
    // The evidence rides along on the report, not just on screen.
    expect(openBugReport.mock.calls[0][0].error.message).toContain('ModuleNotFoundError');
  });

  // A Report click that silently does nothing reads as a broken button — the
  // user is left with no idea whether anything was sent.
  it('tells the user when the report cannot be opened', async () => {
    openBugReport.mockRejectedValueOnce(new Error('no browser'));
    render(<BackendStartFailureNotice />);
    emit(DIAGNOSIS);
    fireEvent.click(await screen.findByRole('button', { name: /see why/i }));
    fireEvent.click(await screen.findByRole('button', { name: /report/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    // …and the diagnosis stays on screen, so the fallback (copy it manually)
    // the message points at is actually available.
    expect((await screen.findByRole('dialog')).textContent).toContain('ModuleNotFoundError');
  });

  it('ignores an empty diagnosis', () => {
    const { container } = render(<BackendStartFailureNotice />);
    emit('   ');
    expect(container).toBeEmptyDOMElement();
  });
});
