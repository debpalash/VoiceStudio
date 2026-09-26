import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { exchangeApiKey } = vi.hoisted(() => ({ exchangeApiKey: vi.fn() }));
vi.mock('../api/authSession', async (importOriginal) => ({
  ...(await importOriginal()),
  exchangeApiKey,
}));

import RemoteAuthGate from './RemoteAuthGate';

describe('RemoteAuthGate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    exchangeApiKey.mockResolvedValue({ transport: 'bearer', expiresAt: Date.now() / 1000 + 60 });
    sessionStorage.clear();
    localStorage.clear();
  });
  afterEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  it('renders children when not gated', () => {
    render(
      <RemoteAuthGate>
        <div>app-content</div>
      </RemoteAuthGate>,
    );
    expect(screen.getByText('app-content')).toBeInTheDocument();
  });

  it('stores the entered PIN', () => {
    render(
      <RemoteAuthGate forceGate>
        <div>app-content</div>
      </RemoteAuthGate>,
    );
    fireEvent.change(screen.getByLabelText(/access pin/i), { target: { value: '999111' } });
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));
    expect(sessionStorage.getItem('ov_pin')).toBe('999111');
  });

  it('coalesces repeated PIN submissions while persistence-aware reload is pending', async () => {
    let finishReload;
    const reload = vi.fn(() => new Promise((resolve) => (finishReload = resolve)));
    render(
      <RemoteAuthGate forceGate reload={reload}>
        <div>app-content</div>
      </RemoteAuthGate>,
    );
    fireEvent.change(screen.getByLabelText(/access pin/i), { target: { value: '999111' } });
    const button = screen.getByRole('button', { name: /connect/i });
    const form = button.closest('form');

    fireEvent.submit(form);
    fireEvent.submit(form);

    expect(reload).toHaveBeenCalledOnce();
    expect(button).toBeDisabled();
    finishReload();
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it('exchanges the entered API key without persisting the master (apikey mode)', async () => {
    render(
      <RemoteAuthGate forceGate forceMode="apikey">
        <div>app-content</div>
      </RemoteAuthGate>,
    );
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'secret123' } });
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await waitFor(() => expect(exchangeApiKey).toHaveBeenCalledOnce());
    expect(exchangeApiKey).toHaveBeenCalledWith(
      'secret123',
      expect.objectContaining({ apiBase: expect.any(String) }),
    );
    expect(localStorage.getItem('ov_api_key')).toBeNull();
    expect(sessionStorage.getItem('ov_api_key')).toBeNull();
  });

  it('does not retain or reflect the master when exchange fails', async () => {
    exchangeApiKey.mockRejectedValueOnce(Object.assign(new Error('generic'), { status: 401 }));
    render(
      <RemoteAuthGate forceGate forceMode="apikey">
        <div>app-content</div>
      </RemoteAuthGate>,
    );
    const input = screen.getByLabelText(/api key/i);
    fireEvent.change(input, { target: { value: 'do-not-reflect' } });
    fireEvent.click(screen.getByRole('button', { name: /connect/i }));

    await screen.findByRole('alert');
    expect(input).toHaveValue('');
    expect(screen.queryByText(/do-not-reflect/)).not.toBeInTheDocument();
    expect(localStorage.getItem('ov_api_key')).toBeNull();
  });

  it('coalesces repeated submissions while an exchange is pending', async () => {
    let resolveExchange;
    exchangeApiKey.mockImplementationOnce(
      () => new Promise((resolve) => (resolveExchange = resolve)),
    );
    render(
      <RemoteAuthGate forceGate forceMode="apikey">
        <div>app-content</div>
      </RemoteAuthGate>,
    );
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'secret123' } });
    const button = screen.getByRole('button', { name: /connect/i });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(exchangeApiKey).toHaveBeenCalledOnce();
    expect(button).toBeDisabled();
    resolveExchange({ transport: 'bearer', expiresAt: Date.now() / 1000 + 60 });
    await waitFor(() => expect(button).not.toBeDisabled());
  });
});
