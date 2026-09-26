import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const openWatchSource = vi.fn();
vi.mock('../utils/watchFolder', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    // Fast poll so tests exercise the real interval loop without fake timers.
    WATCH_POLL_MS: 20,
    openWatchSource: (...args) => openWatchSource(...args),
  };
});

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('react-hot-toast', () => ({
  default: { success: (...a) => toastSuccess(...a), error: (...a) => toastError(...a) },
}));

import WatchFolderBar from './WatchFolderBar';

/** In-memory watch source standing in for the Tauri/FS-Access backends. */
function makeSource(label = 'Drop') {
  const files = new Map(); // name → {size, mtime, bytes}
  return {
    label,
    path: `/watched/${label}`,
    files,
    closed: false,
    listEntries: vi.fn(async () =>
      [...files.entries()].map(([name, f]) => ({ name, size: f.size, mtime: f.mtime })),
    ),
    readFile: vi.fn(
      async (entry) => new File([files.get(entry.name).bytes], entry.name, { type: 'video/mp4' }),
    ),
    close() {
      this.closed = true;
    },
  };
}

const settle = () => new Promise((r) => setTimeout(r, 70)); // > 2 polls at 20ms

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('WatchFolderBar', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('auto-ingests a new video as File objects after it settles, exactly once', async () => {
    const source = makeSource();
    openWatchSource.mockResolvedValue(source);
    const onIngest = vi.fn().mockResolvedValue(undefined);
    render(<WatchFolderBar onIngest={onIngest} />);

    fireEvent.click(screen.getByText('Watch folder'));
    await screen.findByTestId('watch-folder-active');

    // Drop a new mp4 into the "folder" — no Add-to-queue interaction at all.
    source.files.set('new clip.mp4', { size: 7, mtime: 42, bytes: 'content' });
    await waitFor(() => expect(onIngest).toHaveBeenCalledTimes(1));

    const files = onIngest.mock.calls[0][0];
    expect(files).toHaveLength(1);
    expect(files[0]).toBeInstanceOf(File);
    expect(files[0].name).toBe('new clip.mp4');
    expect(files[0].size).toBe(7);

    // Unchanged on later polls → deduped, never enqueued twice.
    await settle();
    expect(onIngest).toHaveBeenCalledTimes(1);
    expect(screen.getByText('1 auto-added')).toBeInTheDocument();
  });

  it('does not enqueue files that were already in the folder when watching started', async () => {
    const source = makeSource();
    source.files.set('existing.mp4', { size: 3, mtime: 1, bytes: 'old' });
    openWatchSource.mockResolvedValue(source);
    const onIngest = vi.fn();
    render(<WatchFolderBar onIngest={onIngest} />);

    fireEvent.click(screen.getByText('Watch folder'));
    await screen.findByTestId('watch-folder-active');
    await settle();
    expect(onIngest).not.toHaveBeenCalled();
  });

  it('ignores non-video files', async () => {
    const source = makeSource();
    openWatchSource.mockResolvedValue(source);
    const onIngest = vi.fn();
    render(<WatchFolderBar onIngest={onIngest} />);

    fireEvent.click(screen.getByText('Watch folder'));
    await screen.findByTestId('watch-folder-active');
    source.files.set('notes.txt', { size: 2, mtime: 5, bytes: 'hi' });
    await settle();
    expect(onIngest).not.toHaveBeenCalled();
  });

  it('pause stops ingesting; resume picks new files back up', async () => {
    const source = makeSource();
    openWatchSource.mockResolvedValue(source);
    const onIngest = vi.fn().mockResolvedValue(undefined);
    render(<WatchFolderBar onIngest={onIngest} />);

    fireEvent.click(screen.getByText('Watch folder'));
    await screen.findByTestId('watch-folder-active');

    fireEvent.click(screen.getByText('Pause'));
    source.files.set('while-paused.mp4', { size: 9, mtime: 9, bytes: 'x' });
    await settle();
    expect(onIngest).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText('Resume'));
    await waitFor(() => expect(onIngest).toHaveBeenCalledTimes(1));
    expect(onIngest.mock.calls[0][0][0].name).toBe('while-paused.mp4');
  });

  it('Stop releases the source and returns to the idle button', async () => {
    const source = makeSource();
    openWatchSource.mockResolvedValue(source);
    render(<WatchFolderBar onIngest={vi.fn()} />);
    fireEvent.click(screen.getByText('Watch folder'));
    await screen.findByTestId('watch-folder-active');

    fireEvent.click(screen.getByText('Stop'));
    expect(source.closed).toBe(true);
    expect(screen.getByText('Watch folder')).toBeInTheDocument();
  });

  it('unmount stops the watcher: source closed, polling ends', async () => {
    const source = makeSource();
    openWatchSource.mockResolvedValue(source);
    const { unmount } = render(<WatchFolderBar onIngest={vi.fn()} />);
    fireEvent.click(screen.getByText('Watch folder'));
    await screen.findByTestId('watch-folder-active');

    unmount();
    expect(source.closed).toBe(true);
    const calls = source.listEntries.mock.calls.length;
    await settle();
    expect(source.listEntries.mock.calls.length).toBe(calls);
  });

  it('shows an actionable message where folder watching is unsupported (web-only)', async () => {
    const err = new Error('Folder watching is unavailable in this browser');
    err.code = 'watch-unsupported';
    openWatchSource.mockRejectedValue(err);
    render(<WatchFolderBar onIngest={vi.fn()} />);

    fireEvent.click(screen.getByText('Watch folder'));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        'Folder watching needs the desktop app or a Chromium-based browser — use Add Videos here instead.',
      ),
    );
    // Still idle — nothing started.
    expect(screen.queryByTestId('watch-folder-active')).not.toBeInTheDocument();
  });

  it('a cancelled picker changes nothing', async () => {
    openWatchSource.mockResolvedValue(null);
    render(<WatchFolderBar onIngest={vi.fn()} />);
    fireEvent.click(screen.getByText('Watch folder'));
    await settle();
    expect(screen.queryByTestId('watch-folder-active')).not.toBeInTheDocument();
    expect(toastError).not.toHaveBeenCalled();
  });

  it('closes a native source when its initial directory scan fails', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const source = makeSource();
    source.listEntries.mockRejectedValue(new Error('cannot scan'));
    openWatchSource.mockResolvedValue(source);
    render(<WatchFolderBar onIngest={vi.fn()} />);

    fireEvent.click(screen.getByText('Watch folder'));
    await waitFor(() => expect(source.closed).toBe(true));
    // Stable actionable message; the raw error goes to the console only.
    expect(toastError.mock.calls[0][0]).toMatch(/pick it again to retry/);
    expect(toastError.mock.calls[0][0]).not.toContain('cannot scan');
    expect(screen.queryByTestId('watch-folder-active')).not.toBeInTheDocument();
    warn.mockRestore();
  });

  it('closes a source returned after the component unmounts', async () => {
    const pending = deferred();
    const source = makeSource();
    openWatchSource.mockReturnValue(pending.promise);
    const { unmount } = render(<WatchFolderBar onIngest={vi.fn()} />);

    fireEvent.click(screen.getByText('Watch folder'));
    unmount();
    pending.resolve(source);
    await waitFor(() => expect(source.closed).toBe(true));
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('allows only one folder picker while startup is pending', async () => {
    const pending = deferred();
    openWatchSource.mockReturnValue(pending.promise);
    render(<WatchFolderBar onIngest={vi.fn()} />);

    const button = screen.getByText('Watch folder').closest('button');
    fireEvent.click(button);
    fireEvent.click(button);
    expect(openWatchSource).toHaveBeenCalledTimes(1);
    pending.resolve(null);
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it('retries a stable entry after a transient read failure', async () => {
    const source = makeSource();
    openWatchSource.mockResolvedValue(source);
    const onIngest = vi.fn().mockResolvedValue(1);
    render(<WatchFolderBar onIngest={onIngest} />);
    fireEvent.click(screen.getByText('Watch folder'));
    await screen.findByTestId('watch-folder-active');

    source.files.set('retry.mp4', { size: 5, mtime: 7, bytes: 'video' });
    source.readFile.mockRejectedValueOnce(new Error('temporarily locked'));
    await waitFor(() => expect(source.readFile).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(onIngest).toHaveBeenCalledTimes(1));
    expect(screen.getByText('1 auto-added')).toBeInTheDocument();
  });

  it('retries when the queue rejects an otherwise readable entry', async () => {
    const source = makeSource();
    openWatchSource.mockResolvedValue(source);
    const onIngest = vi.fn().mockResolvedValueOnce(0).mockResolvedValueOnce(1);
    render(<WatchFolderBar onIngest={onIngest} />);
    fireEvent.click(screen.getByText('Watch folder'));
    await screen.findByTestId('watch-folder-active');

    source.files.set('retry.mp4', { size: 5, mtime: 7, bytes: 'video' });
    await waitFor(() => expect(onIngest).toHaveBeenCalledTimes(2));
    expect(screen.getByText('1 auto-added')).toBeInTheDocument();
  });

  it('stops loudly with an actionable message when the watched folder becomes unreadable', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const source = makeSource();
    openWatchSource.mockResolvedValue(source);
    render(<WatchFolderBar onIngest={vi.fn()} />);
    fireEvent.click(screen.getByText('Watch folder'));
    await screen.findByTestId('watch-folder-active');

    source.listEntries.mockRejectedValue(new Error('EACCES: permission denied /watched/Drop'));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    // The toast is a stable actionable string, and the console warning is a
    // sanitized code — neither may carry technical detail or filesystem paths.
    expect(toastError.mock.calls[0][0]).toMatch(/Pick it again to resume/);
    expect(toastError.mock.calls[0][0]).not.toContain('EACCES');
    expect(JSON.stringify(warn.mock.calls)).not.toContain('/watched/Drop');
    expect(source.closed).toBe(true);
    expect(screen.getByText('Watch folder')).toBeInTheDocument();
    warn.mockRestore();
  });

  it('a poll that straddles unmount neither enqueues nor toasts', async () => {
    const source = makeSource();
    const realList = source.listEntries.getMockImplementation();
    openWatchSource.mockResolvedValue(source);
    const onIngest = vi.fn().mockResolvedValue(undefined);
    const { unmount } = render(<WatchFolderBar onIngest={onIngest} />);
    fireEvent.click(screen.getByText('Watch folder'));
    await screen.findByTestId('watch-folder-active');

    // A file settles across two normal scans' worth of state, then the scan
    // that would enqueue it hangs across the unmount.
    const gates = [];
    source.listEntries.mockImplementation(
      () => new Promise((resolve) => gates.push(() => resolve(realList()))),
    );
    source.files.set('late.mp4', { size: 5, mtime: 3, bytes: 'late' });
    await waitFor(() => expect(gates.length).toBe(1));
    gates[0](); // pending
    await waitFor(() => expect(gates.length).toBe(2));
    unmount(); // …and the in-flight releasing scan resolves afterwards
    gates[1]();
    await settle();
    expect(onIngest).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
    expect(source.closed).toBe(true);
  });

  it('a file arriving during a poll that straddles Pause is NOT enqueued until Resume', async () => {
    const source = makeSource();
    const realList = source.listEntries.getMockImplementation();
    openWatchSource.mockResolvedValue(source);
    const onIngest = vi.fn().mockResolvedValue(undefined);
    render(<WatchFolderBar onIngest={onIngest} />);
    fireEvent.click(screen.getByText('Watch folder'));
    await screen.findByTestId('watch-folder-active');

    // Gate every scan after the prime so each poll is released explicitly.
    const gates = [];
    source.listEntries.mockImplementation(
      () => new Promise((resolve) => gates.push(() => resolve(realList()))),
    );
    source.files.set('raced.mp4', { size: 5, mtime: 3, bytes: 'raced' });

    // Scan 1: the new file becomes a pending (settling) candidate.
    await waitFor(() => expect(gates.length).toBe(1));
    gates[0]();
    // Scan 2 is the one that would release + enqueue it. While it is still
    // in flight, the user pauses — then the scan completes.
    await waitFor(() => expect(gates.length).toBe(2));
    fireEvent.click(screen.getByText('Pause'));
    gates[1]();
    await settle();
    expect(onIngest).not.toHaveBeenCalled(); // nothing enters the queue while paused

    // Resume: the handed-back file is ingested on the next poll, exactly once.
    source.listEntries.mockImplementation(realList);
    fireEvent.click(screen.getByText('Resume'));
    await waitFor(() => expect(onIngest).toHaveBeenCalledTimes(1));
    expect(onIngest.mock.calls[0][0][0].name).toBe('raced.mp4');
    await settle();
    expect(onIngest).toHaveBeenCalledTimes(1);
  });
});
