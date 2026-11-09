import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', async (load) => ({
  ...(await load<typeof import('@/lib/api/client')>()),
  apiJson: mock.api,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, unknown>) =>
      vars ? `${key} ${JSON.stringify(vars)}` : key,
    i18n: { resolvedLanguage: 'en', language: 'en' },
  }),
}));

import { CloudSyncPanel } from './cloud-sync-panel';

const baseStatus = {
  enabled: true,
  running: true,
  device_id: 'DvLocal000000001',
  choices: { settings: true, shortcuts: true, favorites: true },
  collections: [
    { name: 'settings', entitled: true, enabled: true },
    { name: 'shortcuts', entitled: true, enabled: false },
    { name: 'favorites', entitled: true, enabled: true },
    { name: 'voices', entitled: false, enabled: true },
  ],
  usage: {
    used_bytes: 2048,
    limit_bytes: 10485760,
    collections: [{ name: 'favorites', items: 1, bytes: 2048 }],
  },
  other_devices: [],
  status: {
    conflicts: [],
    needs_update: [],
    paused: {},
    storage_full: false,
    read_only: false,
    offline: false,
    stopped: null,
    pending_mass_delete: null,
    last_sync_at: null,
  },
};

function show(
  status: Record<string, unknown>,
  devices: unknown[] = [],
  props: { planUrl?: string | null } = {},
) {
  mock.api.mockImplementation(async (path: string) => {
    if (path === '/api/cloud-sync/status')
      return {
        ...baseStatus,
        ...status,
        status: { ...baseStatus.status, ...(status.status as object) },
      };
    if (path === '/api/cloud-sync/devices') return { devices };
    return { ...baseStatus, ...status };
  });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <CloudSyncPanel {...props} />
    </QueryClientProvider>,
  );
}
const posted = (path: string) => mock.api.mock.calls.filter(([p, i]) => p === path && i?.method);
afterEach(() => {
  cleanup();
  mock.api.mockReset();
});

it('offers choices before sync is set up, settings on by default', async () => {
  show({ enabled: false, choices: { settings: false, shortcuts: false, favorites: false } });
  fireEvent.click(await screen.findByText('cloudsync.turn_on'));
  await waitFor(() => expect(posted('/api/cloud-sync/on')).toHaveLength(1));
  expect(JSON.parse(posted('/api/cloud-sync/on')[0][1].body)).toEqual({
    choices: { settings: true, shortcuts: false, favorites: false },
  });
});

it('switching a toggle off asks whether to also remove it from the cloud', async () => {
  show({});
  fireEvent.click(await screen.findByRole('switch', { name: 'cloudsync.favorites' }));
  expect(screen.getByText(/cloudsync.turned_off_prompt/)).toBeInTheDocument();
  fireEvent.click(screen.getByText(/cloudsync.remove_from_cloud/));
  await waitFor(() => expect(posted('/api/cloud-sync/choices')).toHaveLength(1));
  expect(JSON.parse(posted('/api/cloud-sync/choices')[0][1].body)).toEqual({
    choices: { settings: true, shortcuts: true, favorites: false },
    remove_from_cloud: ['favorites'],
  });
});

it('keeping data in the cloud only changes the choice', async () => {
  show({});
  fireEvent.click(await screen.findByRole('switch', { name: 'cloudsync.settings' }));
  fireEvent.click(screen.getByText('cloudsync.keep_in_cloud'));
  await waitFor(() => expect(posted('/api/cloud-sync/choices')).toHaveLength(1));
  expect(JSON.parse(posted('/api/cloud-sync/choices')[0][1].body).remove_from_cloud).toEqual([]);
});

it('shows Pro locks and paused collections', async () => {
  show({});
  expect(await screen.findByText('cloudsync.voices')).toBeInTheDocument();
  expect(screen.getByText('cloudsync.pro')).toBeInTheDocument();
  expect(screen.getByText('cloudsync.paused')).toBeInTheDocument();
});

it('a removed device can be set up again', async () => {
  show({ status: { stopped: 'device_revoked' } });
  expect(await screen.findByText('cloudsync.device_revoked')).toBeInTheDocument();
  fireEvent.click(screen.getByText('cloudsync.set_up_again'));
  await waitFor(() => expect(posted('/api/cloud-sync/on')).toHaveLength(1));
  expect(JSON.parse(posted('/api/cloud-sync/on')[0][1].body).new_device).toBe(true);
});

it('a big delete waits for the user to confirm it', async () => {
  show({ status: { pending_mass_delete: { count: 25 } } });
  fireEvent.click(await screen.findByText('cloudsync.confirm_remove'));
  await waitFor(() => expect(posted('/api/cloud-sync/confirm-deletes')).toHaveLength(1));
});

it('lists devices and removes one', async () => {
  show({}, [
    {
      id: 'Lp0aB1cD2eF3gH4i',
      name: 'MacBook',
      platform: 'macos',
      current: false,
      last_seen_at: '2026-10-15T22:14:03Z',
    },
  ]);
  expect(await screen.findByText('MacBook')).toBeInTheDocument();
  fireEvent.click(screen.getByText('cloudsync.remove_device'));
  await waitFor(() =>
    expect(posted('/api/cloud-sync/devices/Lp0aB1cD2eF3gH4i/revoke')).toHaveLength(1),
  );
});

it('offers another device’s choices', async () => {
  show({ other_devices: [{ device_id: 'Lp0aB1cD2eF3gH4i', choices: { settings: true } }] }, [
    { id: 'Lp0aB1cD2eF3gH4i', name: 'MacBook', platform: 'macos', current: false },
  ]);
  fireEvent.click(await screen.findByText(/cloudsync.use_choices_of/));
  await waitFor(() =>
    expect(posted('/api/cloud-sync/use-choices-of/Lp0aB1cD2eF3gH4i')).toHaveLength(1),
  );
});

it('reports a full cloud, offline state and conflicts', async () => {
  show({
    status: {
      storage_full: true,
      offline: true,
      conflicts: [{ collection: 'settings', item_id: 'appearance', keys: ['theme'] }],
    },
  });
  expect(await screen.findByText(/cloudsync.storage_full/)).toBeInTheDocument();
  expect(screen.getByText('account.offline')).toBeInTheDocument();
  expect(screen.getByText(/cloudsync.conflict/)).toBeInTheDocument();
});

it('lists each device with its OS and last-seen date, and removes only other devices', async () => {
  show({}, [
    {
      id: 'DvLocal000000001',
      name: 'Studio PC',
      platform: 'linux',
      os_version: 'Linux 6.12',
      current: true,
      last_seen_at: '2026-10-16T09:00:00Z',
    },
    {
      id: 'Lp0aB1cD2eF3gH4i',
      name: 'MacBook',
      platform: 'macos',
      os_version: 'macOS 15.1',
      current: false,
      last_seen_at: '2026-10-15T22:14:03Z',
    },
  ]);
  expect(await screen.findByText('MacBook')).toBeInTheDocument();
  expect(screen.getByText(/macOS 15\.1/)).toBeInTheDocument();
  expect(screen.getByText(/Linux 6\.12/)).toBeInTheDocument();
  expect(screen.getAllByText('cloudsync.remove_device')).toHaveLength(1);
});

it('marks Pro-only collections with a badge, locks their switch and upsells once', async () => {
  show(
    {
      collections: [
        ...baseStatus.collections,
        { name: 'takes', entitled: false, enabled: true },
        { name: 'transcriptions', entitled: true, enabled: true },
      ],
    },
    [],
    { planUrl: 'https://voicestudio.example/plan' },
  );
  expect(await screen.findByRole('switch', { name: 'cloudsync.voices' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  // Two locked collections, two badges, one upsell with one plan link.
  expect(screen.getAllByTestId('sync-pro-badge')).toHaveLength(2);
  expect(screen.getAllByTestId('sync-pro-upsell')).toHaveLength(1);
  expect(screen.getByRole('link', { name: 'supportPlans.get_pro' })).toHaveAttribute(
    'href',
    'https://voicestudio.example/plan',
  );
  expect(screen.getByRole('switch', { name: 'cloudsync.voices' })).toHaveAttribute(
    'aria-describedby',
    'sync-pro-upsell',
  );
  // An entitled collection carries no Pro badge or lock description.
  expect(screen.getByRole('switch', { name: 'cloudsync.dictation_history' })).not.toHaveAttribute(
    'aria-describedby',
  );
  // The free collections stay usable.
  expect(screen.getByRole('switch', { name: 'cloudsync.settings' })).not.toHaveAttribute(
    'aria-disabled',
    'true',
  );
});

it('shows no upsell when every listed collection is on the plan', async () => {
  show({ collections: [{ name: 'voices', entitled: true, enabled: true }] });
  expect(await screen.findByText('cloudsync.voices')).toBeInTheDocument();
  expect(screen.queryByTestId('sync-pro-badge')).toBeNull();
  expect(screen.queryByTestId('sync-pro-upsell')).toBeNull();
});

it('formats a device row with a This device badge, its OS and a relative last-active time', async () => {
  const twoDaysAgo = new Date(Date.now() - 2 * 86_400_000).toISOString();
  show({}, [
    {
      id: 'DvLocal000000001',
      name: 'Studio PC',
      platform: 'windows',
      os_version: 'Windows 10.0.26100',
      current: true,
      last_seen_at: twoDaysAgo,
    },
  ]);
  expect(await screen.findByText('Studio PC')).toBeInTheDocument();
  expect(screen.getByText('cloudsync.this_device')).toBeInTheDocument();
  const meta = screen.getByText(/Windows 10\.0\.26100/);
  expect(meta).toHaveTextContent('cloudsync.last_active {"time":"2 days ago"}');
  // No locale-specific absolute date such as 11/7/2026.
  expect(meta.textContent).not.toMatch(/\d+\/\d+\/\d{4}/);
  expect(screen.queryByText('cloudsync.remove_device')).toBeNull();
});
