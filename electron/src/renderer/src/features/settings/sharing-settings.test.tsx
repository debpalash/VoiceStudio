import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
  api: vi.fn(),
  qr: vi.fn(() => Promise.resolve('data:image/png;base64,qr')),
  openExternal: vi.fn(),
}));

vi.mock('@/lib/api/client', async (load) => {
  const actual = await load<typeof import('@/lib/api/client')>();
  return { ...actual, apiJson: mock.api };
});
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { openExternal: mock.openExternal } }),
}));
vi.mock('qrcode', () => ({ default: { toDataURL: mock.qr } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { SharingSettings } from './sharing-settings';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderSettings(overrides: { tailscale?: object; uiPort?: number | null } = {}) {
  let state: {
    enabled: boolean;
    share_port: number | null;
    pin: string | null;
    lan_addresses: string[];
  } = { enabled: false, share_port: null, pin: null, lan_addresses: [] };
  mock.api.mockImplementation((path: string, init?: RequestInit) => {
    if (path === '/system/network/state') return Promise.resolve(state);
    if (path === '/system/info') {
      return Promise.resolve({
        backend_port: 3900,
        ui_port: overrides.uiPort === undefined ? 3902 : overrides.uiPort,
        share_port_base: 3901,
      });
    }
    if (path === '/system/tailscale/status') {
      return Promise.resolve(overrides.tailscale ?? { installed: false, running: false });
    }
    if (path === '/system/network/enable' && init?.method === 'POST') {
      state = {
        enabled: true,
        share_port: 3901,
        pin: '123456',
        lan_addresses: ['192.168.1.4'],
      };
      return Promise.resolve(state);
    }
    if (path === '/system/network/disable' && init?.method === 'POST') {
      state = { enabled: false, share_port: null, pin: null, lan_addresses: [] };
      return Promise.resolve(state);
    }
    if (path === '/system/tailscale/enable' && init?.method === 'POST') {
      return Promise.resolve({ ok: true, url: 'https://studio.tailnet.ts.net', note: 'Private' });
    }
    return Promise.resolve({});
  });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <SharingSettings />
    </QueryClientProvider>,
  );
}

it('confirms LAN exposure, shows its PIN/QR, and stops the listener', async () => {
  renderSettings();

  const share = await screen.findByRole('button', { name: 'network.share_on_network' });
  await waitFor(() => expect(share).toBeEnabled());
  fireEvent.click(share);
  expect(screen.getByText('network.share_confirm_hint')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'network.enable' }));

  expect(await screen.findByText('192.168.1.4:3901')).toBeInTheDocument();
  expect(screen.getByText('123456')).toBeInTheDocument();
  expect(await screen.findByRole('img', { name: 'network.qr_alt' })).toHaveAttribute(
    'src',
    'data:image/png;base64,qr',
  );
  expect(mock.qr).toHaveBeenCalledWith('http://192.168.1.4:3901/?pin=123456');

  fireEvent.click(screen.getByRole('button', { name: 'network.stop_sharing' }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/system/network/disable', { method: 'POST' }),
  );
  expect(await screen.findByRole('button', { name: 'network.share_on_network' })).toBeEnabled();
});

it('saves the share port and exposes explicit Tailscale controls', async () => {
  renderSettings({ tailscale: { installed: true, running: true } });

  const port = await screen.findByRole('spinbutton', { name: 'sharing.lan_share_port' });
  expect(port).toHaveValue(3901);
  fireEvent.change(port, { target: { value: '4101' } });
  fireEvent.click(screen.getByRole('button', { name: 'common.save' }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/system/set-env', {
      method: 'POST',
      body: JSON.stringify({ key: 'OMNIVOICE_SHARE_PORT', value: '4101' }),
    }),
  );

  fireEvent.click(screen.getByRole('button', { name: 'sharing.tailscale_enable_btn' }));
  expect(await screen.findByText('https://studio.tailnet.ts.net')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'sharing.tailscale_open' }));
  expect(mock.openExternal).toHaveBeenCalledWith('https://studio.tailnet.ts.net');
});

it('shows the dev UI port and hides it for the packaged app', async () => {
  renderSettings();
  expect(await screen.findByText('3902')).toBeInTheDocument();
  expect(screen.getByText('sharing.ui_port')).toBeInTheDocument();
  cleanup();

  renderSettings({ uiPort: null });
  expect(await screen.findByText('sharing.backend_port')).toBeInTheDocument();
  expect(screen.queryByText('sharing.ui_port')).not.toBeInTheDocument();
});
