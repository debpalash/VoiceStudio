/**
 * The one-time-secret block is the only place a join code or connection string
 * ever exists outside the machine that minted it, so these pin the two ways it
 * can lose one: a QR that fails to render taking the code with it, and a copy
 * button that reports success it did not have.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const { toDataURL } = vi.hoisted(() => ({ toDataURL: vi.fn() }));
vi.mock('qrcode', () => ({ default: { toDataURL } }));

const { copyText } = vi.hoisted(() => ({ copyText: vi.fn() }));
vi.mock('../utils/copyText', () => ({ copyText }));

import OneTimeSecret from './OneTimeSecret';

beforeEach(() => {
  vi.clearAllMocks();
  toDataURL.mockResolvedValue('data:image/png;base64,QR');
  copyText.mockResolvedValue(true);
});

afterEach(() => vi.useRealTimers());

describe('OneTimeSecret', () => {
  it('shows the secret and a QR carrying exactly the same string', async () => {
    render(<OneTimeSecret value="ovw_abc123" headline="Copy this now" />);

    expect(screen.getByText('ovw_abc123')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('img')).toHaveAttribute('src', expect.any(String)));
    expect(toDataURL.mock.calls[0][0]).toBe('ovw_abc123');
  });

  it('still renders the code when the QR cannot be generated', async () => {
    // A string past the format's capacity rejects. Losing the QR is a
    // degraded share; losing the only copy of the secret is data loss.
    toDataURL.mockRejectedValue(new Error('too long'));
    render(<OneTimeSecret value="ovnode://very-long" headline="Copy this now" />);

    await waitFor(() => expect(screen.queryByRole('img')).not.toBeInTheDocument());
    expect(screen.getByText('ovnode://very-long')).toBeInTheDocument();
    expect(screen.getByText('Copy')).toBeInTheDocument();
  });

  it('drops to low error correction for a long string', async () => {
    render(<OneTimeSecret value={'x'.repeat(240)} headline="h" />);
    await waitFor(() => expect(toDataURL).toHaveBeenCalled());
    expect(toDataURL.mock.calls[0][1]).toMatchObject({ errorCorrectionLevel: 'L' });
  });

  it('only reports Copied when the clipboard actually took it', async () => {
    copyText.mockResolvedValue(false);
    render(<OneTimeSecret value="ovw_abc123" headline="h" />);

    fireEvent.click(screen.getByText('Copy'));

    await waitFor(() => expect(copyText).toHaveBeenCalledWith('ovw_abc123'));
    expect(screen.queryByText('Copied')).not.toBeInTheDocument();
  });

  it('counts down to the expiry the control plane gave it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    render(<OneTimeSecret value="ovw_abc" headline="h" expiresAt={Date.now() / 1000 + 125} />);

    expect(await screen.findByText(/2:05/)).toBeInTheDocument();
  });

  it('says the code is dead once it expires, rather than showing 0:00', async () => {
    render(<OneTimeSecret value="ovw_abc" headline="h" expiresAt={Date.now() / 1000 - 1} />);
    expect(await screen.findByText(/Expired/)).toBeInTheDocument();
  });
});
