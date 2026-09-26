import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import SharingPanel from './SharingPanel';

describe('SharingPanel', () => {
  let realFetch;
  beforeEach(() => {
    realFetch = global.fetch;
  });
  afterEach(() => {
    global.fetch = realFetch;
  });

  it('shows Tailscale "not detected" when CLI absent', async () => {
    global.fetch = vi.fn((url) => {
      if (String(url).includes('tailscale/status'))
        return Promise.resolve({ ok: true, json: async () => ({ installed: false }) });
      if (String(url).includes('/system/info'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ backend_port: 3900, share_port_base: 3901, ui_port: 3901 }),
        });
      return Promise.resolve({ ok: true, json: async () => ({ enabled: false }) });
    });
    render(<SharingPanel />);
    // Both the explanatory copy and the install button surface the phrase, so
    // assert at least one node renders rather than requiring a unique match.
    await waitFor(() =>
      expect(screen.getAllByText(/not detected|install tailscale/i).length).toBeGreaterThan(0),
    );
  });

  it('renders the ports subsection with values from /system/info', async () => {
    global.fetch = vi.fn((url) => {
      if (String(url).includes('tailscale/status'))
        return Promise.resolve({ ok: true, json: async () => ({ installed: false }) });
      if (String(url).includes('/system/info'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ backend_port: 4000, share_port_base: 4001, ui_port: 4100 }),
        });
      return Promise.resolve({ ok: true, json: async () => ({ enabled: false }) });
    });
    render(<SharingPanel />);
    await waitFor(() => expect(screen.getByTestId('sharing-ports')).toBeInTheDocument());
    expect(screen.getByTestId('port-backend')).toHaveTextContent('4000');
    expect(screen.getByTestId('port-ui')).toHaveTextContent('4100');
    // LAN-share port is an editable input pre-filled with share_port_base.
    expect(screen.getByTestId('port-share-input')).toHaveValue(4001);
    // Env-var names are surfaced for the user.
    expect(screen.getByText('OMNIVOICE_PORT')).toBeInTheDocument();
    expect(screen.getByText('OMNIVOICE_SHARE_PORT')).toBeInTheDocument();
    expect(screen.getByText('OMNIVOICE_UI_PORT')).toBeInTheDocument();
  });
});
