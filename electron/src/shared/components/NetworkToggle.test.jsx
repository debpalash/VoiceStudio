// electron/src/shared/components/NetworkToggle.test.jsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import NetworkToggle from './NetworkToggle';

describe('NetworkToggle', () => {
  let realFetch;
  beforeEach(() => {
    realFetch = global.fetch;
  });
  afterEach(() => {
    global.fetch = realFetch;
  });

  it('defaults to Local when state reports disabled', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: async () => ({ enabled: false }) }),
    );
    render(<NetworkToggle />);
    await waitFor(() => expect(screen.getByText(/local/i)).toBeInTheDocument());
  });

  it('Local → in-app confirm → Enable calls the enable endpoint (regression: no window.confirm)', async () => {
    const posts = [];
    global.fetch = vi.fn((url, opts) => {
      const u = String(url);
      if ((opts?.method || 'GET') === 'POST') posts.push(u);
      if (u.endsWith('/system/network/enable')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ enabled: true, share_port: 5050, pin: '123456', lan_addresses: [] }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({ enabled: false }) });
    });

    render(<NetworkToggle />);
    const pill = await screen.findByRole('button', { name: /local/i });
    fireEvent.click(pill); // opens the in-app confirm — must NOT depend on window.confirm
    const enableBtn = await screen.findByRole('button', { name: /^enable$/i });
    fireEvent.click(enableBtn);

    await waitFor(() => expect(posts.some((u) => u.endsWith('/system/network/enable'))).toBe(true));
  });
});
