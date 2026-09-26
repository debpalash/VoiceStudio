import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock('../../api/client', () => ({
  apiJson: vi.fn(),
  apiFetch: vi.fn(),
}));
const invoke = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...args) => invoke(...args) }));

import { toast } from 'react-hot-toast';
import { apiJson, apiFetch } from '../../api/client';
import AudioToolsPanel from './AudioToolsPanel';

const STATUS = {
  ready: true,
  platform_key: 'darwin_arm64',
  tools: {
    ffmpeg: {
      tool: 'ffmpeg',
      ok: true,
      path: '/data/media_tools/ffbin-abc/darwin_arm64/ffmpeg',
      version: '7.0',
      origin: 'bundled',
    },
    ffprobe: {
      tool: 'ffprobe',
      ok: true,
      path: '/opt/homebrew/bin/ffprobe',
      version: '8.1.1',
      origin: 'system',
    },
    ytdlp: {
      tool: 'yt-dlp',
      ok: true,
      path: '/venv/site-packages/yt_dlp',
      version: '2026.06.09',
      origin: 'bundled',
      overlay_version: null,
      baseline_version: null,
    },
  },
  ops: {
    acquire: { state: 'idle', progress: 0, error: null },
    ytdlp_update: { state: 'idle', progress: 0, error: null, version: null },
  },
};

const okResponse = { ok: true, json: async () => ({}) };

describe('AudioToolsPanel — power-user surface for the media tools', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.__TAURI_INTERNALS__ = {};
    apiJson.mockResolvedValue(JSON.parse(JSON.stringify(STATUS)));
    apiFetch.mockResolvedValue(okResponse);
    invoke.mockResolvedValue({ authorization: 'c'.repeat(64), path: '/opt/tools/ffmpeg' });
  });

  it('does not offer native file selection in a browser', async () => {
    delete window.__TAURI_INTERNALS__;
    render(<AudioToolsPanel />);
    await screen.findByText('FFmpeg');
    expect(screen.queryByLabelText('FFmpeg: Choose file…')).not.toBeInTheDocument();
  });

  it('renders one row per tool with version, path, and origin badge', async () => {
    render(<AudioToolsPanel />);
    await waitFor(() => expect(apiJson).toHaveBeenCalledWith('/media-tools/status'));

    expect(await screen.findByText('FFmpeg')).toBeInTheDocument();
    expect(screen.getByText('FFprobe')).toBeInTheDocument();
    expect(screen.getByText('yt-dlp (video downloader)')).toBeInTheDocument();

    // ffmpeg + yt-dlp are both app-managed here; ffprobe is a system copy.
    const bundled = screen.getAllByTestId('origin-bundled');
    expect(bundled).toHaveLength(2);
    expect(bundled[0]).toHaveTextContent('Bundled');
    expect(screen.getByTestId('origin-system')).toHaveTextContent('System');
    expect(screen.getByText('/opt/homebrew/bin/ffprobe')).toBeInTheDocument();
    expect(screen.getByText(/2026\.06\.09/)).toBeInTheDocument();
  });

  it('Use system copy posts the endpoint and toasts success', async () => {
    render(<AudioToolsPanel />);
    fireEvent.click(await screen.findByLabelText('FFmpeg: Use system copy'));
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith('/media-tools/ffmpeg/use-system', expect.anything()),
    );
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });

  it('sends only a native one-shot authorization for a custom executable', async () => {
    render(<AudioToolsPanel />);
    fireEvent.click(await screen.findByLabelText('FFmpeg: Choose file…'));

    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith('authorize_host_path', {
        kind: 'ffmpeg',
      }),
    );
    expect(apiFetch).toHaveBeenCalledWith(
      '/media-tools/ffmpeg/custom-path',
      expect.objectContaining({ body: JSON.stringify({ authorization: 'c'.repeat(64) }) }),
    );
    expect(apiFetch.mock.calls.flat().join(' ')).not.toContain('/opt/tools/ffmpeg');
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('/opt/tools/ffmpeg'));
  });

  it('Restore bundled is per-tool and always available (safe revert)', async () => {
    render(<AudioToolsPanel />);
    fireEvent.click(await screen.findByLabelText('FFprobe: Restore bundled'));
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith('/media-tools/ffprobe/restore', expect.anything()),
    );
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });

  it('surfaces the backend error detail on a failed action', async () => {
    apiFetch.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: 'That file exists but does not run as a media tool' }),
    });
    render(<AudioToolsPanel />);
    fireEvent.click(await screen.findByLabelText('FFmpeg: Use system copy'));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(String(toast.error.mock.calls[0][0])).toContain('does not run as a media tool');
  });

  it('yt-dlp row: Update posts the update endpoint', async () => {
    render(<AudioToolsPanel />);
    fireEvent.click(await screen.findByLabelText('yt-dlp: Update'));
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith('/media-tools/ytdlp/update', expect.anything()),
    );
  });

  it('yt-dlp row: Restore tested version appears only when an overlay is active', async () => {
    const { unmount } = render(<AudioToolsPanel />);
    await screen.findByText('yt-dlp (video downloader)');
    expect(screen.queryByTestId('ytdlp-restore')).not.toBeInTheDocument();
    unmount();

    const overlaid = JSON.parse(JSON.stringify(STATUS));
    overlaid.tools.ytdlp.origin = 'custom';
    overlaid.tools.ytdlp.overlay_version = '2026.07.01';
    overlaid.tools.ytdlp.version = '2026.07.01';
    overlaid.tools.ytdlp.baseline_version = '2026.06.09';
    apiJson.mockResolvedValue(overlaid);

    render(<AudioToolsPanel />);
    const restore = await screen.findByTestId('ytdlp-restore');
    expect(restore).toHaveTextContent('Restore tested version (2026.06.09)');
    fireEvent.click(restore);
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith('/media-tools/ytdlp/restore', expect.anything()),
    );
  });

  it('section header offers Update bundled build (one download covers both binaries)', async () => {
    render(<AudioToolsPanel />);
    fireEvent.click(await screen.findByLabelText('Update bundled build'));
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith('/media-tools/acquire', expect.anything()),
    );
  });

  it('package-manager commands are copy-only prose, never buttons', async () => {
    render(<AudioToolsPanel />);
    await screen.findByText('FFmpeg');
    // The InfoHint copy mentions brew/apt as a secondary affordance, but no
    // button/control runs a package manager.
    const buttons = screen.getAllByRole('button').map((b) => b.textContent || '');
    expect(buttons.join(' ')).not.toMatch(/brew|apt|winget|choco/i);
  });
});
