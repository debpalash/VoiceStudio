import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

vi.mock('../../api/client', () => ({
  apiJson: vi.fn(),
  apiFetch: vi.fn(),
}));

import toast from 'react-hot-toast';
import { apiJson, apiFetch } from '../../api/client';
import HFMirrorPanel from './HFMirrorPanel';

const PRESETS = [
  { label: 'Official (huggingface.co)', url: '' },
  { label: 'hf-mirror.com (community, China)', url: 'https://hf-mirror.com' },
];

// An existing explicit mirror config — loads as the matching MANUAL mode.
const MANUAL_STATE = {
  configured: 'https://hf-mirror.com',
  effective: 'https://hf-mirror.com',
  presets: PRESETS,
  mode: 'manual',
  auto: null,
  auto_opt_out: false,
};

const AUTO_STATE = {
  configured: '',
  effective: '',
  presets: PRESETS,
  mode: 'auto',
  auto: {
    endpoint: 'https://hf-mirror.com',
    reachable: true,
    latency_ms: 87.3,
    checked_at: 1752200000,
    results: [],
  },
  auto_opt_out: false,
};

describe('HFMirrorPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps the panel visible with an error and a Retry when the initial GET fails', async () => {
    // The restricted-network user whose backend GET 500s is exactly the user
    // who needs this panel — it must never silently vanish.
    apiJson.mockRejectedValueOnce(new Error('HTTP 500'));

    render(<HFMirrorPanel />);

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 500');
    expect(screen.getByText('Hugging Face mirror')).toBeInTheDocument();

    // Retry re-fetches and renders the rows.
    apiJson.mockResolvedValueOnce(MANUAL_STATE);
    fireEvent.click(screen.getByTestId('hf-mirror-retry'));

    expect(await screen.findByTestId('hf-mirror-url')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows a loading state while the GET is in flight (never an empty gap)', () => {
    apiJson.mockReturnValue(new Promise(() => {}));
    render(<HFMirrorPanel />);
    expect(screen.getByText('Hugging Face mirror')).toBeInTheDocument();
    expect(screen.getByTestId('hf-mirror-loading')).toBeInTheDocument();
  });

  it('loads an existing explicit config as the matching manual mode — never Auto', async () => {
    apiJson.mockResolvedValue(MANUAL_STATE);
    render(<HFMirrorPanel />);

    const mirror = await screen.findByTestId('hf-preset-https://hf-mirror.com');
    expect(
      screen.getByRole('heading', { name: 'Hugging Face mirror' }).closest('section'),
    ).toHaveClass('models-settings-compact', '!py-[9px]');
    expect(screen.getByText('Mirror preset').closest('[data-slot="setting-row"]')).toHaveClass(
      '!py-[5px]',
    );
    const official = screen.getByTestId('hf-preset-official');
    const auto = screen.getByTestId('hf-preset-auto');
    expect(mirror).toHaveAttribute('aria-pressed', 'true');
    expect(official).toHaveAttribute('aria-pressed', 'false');
    expect(auto).toHaveAttribute('aria-pressed', 'false');
    // Manual mode has no auto-status row.
    expect(screen.queryByTestId('hf-mirror-auto-status')).not.toBeInTheDocument();
  });

  it('labels the custom-URL row in plain language and toasts on save', async () => {
    apiJson.mockResolvedValue(MANUAL_STATE);
    apiFetch.mockResolvedValue({
      json: async () => ({
        ...MANUAL_STATE,
        configured: 'https://mirror.example',
        restart_required: true,
      }),
    });

    render(<HFMirrorPanel />);

    // Plain translated label (HF_ENDPOINT is a subtitle detail, not the title),
    // and the input carries an accessible name.
    const input = await screen.findByLabelText('Custom mirror URL');
    expect(screen.getByText('Custom mirror URL')).toBeInTheDocument();

    fireEvent.change(input, { target: { value: 'https://mirror.example' } });
    fireEvent.click(screen.getByTestId('hf-mirror-save'));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Mirror setting saved'));
    expect(apiFetch).toHaveBeenCalledWith(
      '/api/settings/hf-mirror',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ url: 'https://mirror.example', mode: 'manual' }),
      }),
    );
  });

  it('shows the Auto pick with measured latency, last-checked, and a Test again button', async () => {
    apiJson.mockResolvedValue(AUTO_STATE);
    render(<HFMirrorPanel />);

    const auto = await screen.findByTestId('hf-preset-auto');
    expect(auto).toHaveAttribute('aria-pressed', 'true');

    const status = screen.getByTestId('hf-mirror-auto-status');
    expect(status).toHaveTextContent('hf-mirror.com');
    expect(status).toHaveTextContent('87 ms');
    expect(status).toHaveTextContent('checked');
    expect(screen.getByTestId('hf-mirror-test')).toBeInTheDocument();
  });

  it('shows an honest untested state in Auto mode before the first race', async () => {
    apiJson.mockResolvedValue({ ...AUTO_STATE, auto: null });
    render(<HFMirrorPanel />);

    const status = await screen.findByTestId('hf-mirror-auto-status');
    expect(status).toHaveTextContent('Not tested yet');
  });

  it('Test again POSTs to the test endpoint and updates the shown pick', async () => {
    apiJson.mockResolvedValue(AUTO_STATE);
    apiFetch.mockResolvedValue({
      json: async () => ({
        ...AUTO_STATE,
        auto: { ...AUTO_STATE.auto, endpoint: 'https://huggingface.co', latency_ms: 42 },
      }),
    });

    render(<HFMirrorPanel />);
    fireEvent.click(await screen.findByTestId('hf-mirror-test'));

    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith('/api/settings/hf-mirror/test', { method: 'POST' }),
    );
    await waitFor(() =>
      expect(screen.getByTestId('hf-mirror-auto-status')).toHaveTextContent('huggingface.co'),
    );
  });

  it('clicking Auto saves mode=auto; clicking a preset saves an explicit manual pick', async () => {
    apiJson.mockResolvedValue(MANUAL_STATE);
    apiFetch.mockResolvedValue({
      json: async () => ({ ...AUTO_STATE, restart_required: false }),
    });

    render(<HFMirrorPanel />);
    fireEvent.click(await screen.findByTestId('hf-preset-auto'));

    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith(
        '/api/settings/hf-mirror',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ url: '', mode: 'auto' }),
        }),
      ),
    );
    // Panel reflects the returned Auto state (pick row appears).
    expect(await screen.findByTestId('hf-mirror-auto-status')).toBeInTheDocument();

    apiFetch.mockResolvedValue({
      json: async () => ({ ...MANUAL_STATE, restart_required: true }),
    });
    fireEvent.click(screen.getByTestId('hf-preset-https://hf-mirror.com'));
    await waitFor(() =>
      expect(apiFetch).toHaveBeenLastCalledWith(
        '/api/settings/hf-mirror',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ url: 'https://hf-mirror.com', mode: 'manual' }),
        }),
      ),
    );
  });
});
