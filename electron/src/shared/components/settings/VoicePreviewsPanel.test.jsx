import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../api/client', () => ({
  apiJson: vi.fn(),
  apiFetch: vi.fn(),
}));

import { apiJson, apiFetch } from '../../api/client';
import VoicePreviewsPanel from './VoicePreviewsPanel';

const OFF = { enabled: false, featured_cached: 0, featured_total: 0, checked_seconds_ago: null };
const ON = {
  enabled: true,
  featured_cached: 51,
  featured_total: 51,
  checked_seconds_ago: 2 * 86400,
};

describe('VoicePreviewsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('offers no check button until the user has opted in', async () => {
    apiJson.mockResolvedValue(OFF);
    render(<VoicePreviewsPanel />);
    await waitFor(() => expect(apiJson).toHaveBeenCalled());
    // Nothing to check when nothing may be downloaded — and the line says so.
    expect(screen.queryByTestId('voice-previews-check')).toBeNull();
    expect(screen.getByText(/previews render on this machine/i)).toBeTruthy();
  });

  it('reads "featured set cached · checked 2 days ago", not a count of 1126', async () => {
    apiJson.mockResolvedValue(ON);
    render(<VoicePreviewsPanel />);
    const line = await screen.findByText(/Featured set cached/);
    expect(line.textContent).toMatch(/2 days ago/);
    expect(line.textContent).not.toMatch(/1126/);
  });

  it('turning the toggle on is what asks the backend to download', async () => {
    apiJson.mockResolvedValue(OFF);
    apiFetch.mockResolvedValue({ json: async () => ON });
    render(<VoicePreviewsPanel />);
    await waitFor(() => expect(apiJson).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe('/archetypes/previews');
    expect(JSON.parse(init.body)).toEqual({ enabled: true });
  });

  it('surfaces a rejected manifest instead of failing silently', async () => {
    apiJson.mockResolvedValue({ ...ON, last_error: 'manifest signature does not verify' });
    render(<VoicePreviewsPanel />);
    const error = await screen.findByTestId('voice-previews-error');
    expect(error.textContent).toMatch(/signature/);
  });
});
