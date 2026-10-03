import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const locale = vi.hoisted(() => ({ current: 'en' }));
const status = vi.fn(async () => ({ active: false, configured: true }));
const activate = vi.fn(async () => ({ active: true, configured: true }));
const deactivate = vi.fn(async () => ({ active: false, configured: true }));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ pro: { status, activate, deactivate } }),
}));
vi.mock('react-i18next', async () => {
  const { createInstance } = await import('i18next');
  const { default: en } = await import('@/i18n/locales/en.json');
  const instance = createInstance();
  await instance.init({ lng: 'en', fallbackLng: 'en', resources: { en: { translation: en } } });
  return {
    useTranslation: () => ({
      t: (key: string, options?: Record<string, unknown>) =>
        key === 'proPage.usd_price' ? instance.t(key, { ...options, lng: locale.current }) : key,
    }),
  };
});
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
}));
vi.mock('@/components/external-link', () => ({
  ExternalLink: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import { ProPage } from './pro-page';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  locale.current = 'en';
});

it('shows pricing followed by Pro features', async () => {
  render(<ProPage />);
  await waitFor(() => expect(status).toHaveBeenCalledOnce());
  expect(screen.queryByText('$9')).not.toBeInTheDocument();
  expect(screen.getAllByText('$99')).toHaveLength(2);
  expect(screen.getAllByText('$299')).toHaveLength(2);
  expect(screen.getByRole('link', { name: /proPage.choose_yearly/ })).toHaveAttribute(
    'href',
    'https://voicestudio.sh/pro?plan=yearly&quantity=1',
  );
  expect(screen.getByRole('link', { name: /proPage.choose_lifetime/ })).toHaveAttribute(
    'href',
    'https://voicestudio.sh/pro?plan=lifetime&quantity=1',
  );
  expect(screen.getByRole('link', { name: /proPage.contact_enterprise/ })).toHaveAttribute(
    'href',
    'https://voicestudio.sh/commercial',
  );
  expect(screen.getByText('proPage.enterprise_price')).toBeVisible();
  expect(screen.getByText('proPage.enterprise_support')).toBeVisible();
  expect(screen.getByRole('heading', { level: 2, name: 'proPage.hero_title' })).toBeVisible();
  expect(screen.getByText('proPage.hero_body')).toBeVisible();
  expect(screen.getByRole('heading', { level: 2, name: 'proPage.features_heading' })).toBeVisible();
  expect(screen.getByText('proPage.features_subtitle')).toBeVisible();
  expect(screen.queryAllByText('proPage.recipes_title')).toHaveLength(0);
  expect(screen.queryAllByText('proPage.watch_title')).toHaveLength(0);
  expect(screen.getAllByText('proPage.automation_title')).toHaveLength(2);
  expect(screen.getAllByText('proPage.history_title')).toHaveLength(2);
  expect(screen.getAllByText('proPage.delivery_title')).toHaveLength(2);
  expect(screen.getAllByText('proPage.preflight_title')).toHaveLength(1);
  expect(screen.queryAllByText('proPage.remote_device_title')).toHaveLength(0);
  expect(screen.queryAllByText('proPage.remote_workers_title')).toHaveLength(0);
  expect(screen.queryAllByText('proPage.gpu_share_title')).toHaveLength(0);
  expect(screen.getAllByText('proPage.commercial_title')).toHaveLength(2);
  expect(screen.getAllByText('proPage.lifetime_includes_pro')).toHaveLength(2);
  expect(screen.getByText('proPage.lifetime_pay_once')).toBeVisible();
  expect(screen.getByText('proPage.lifetime_no_subscription')).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'proPage.activate_title' }));
  expect(screen.getByRole('dialog', { name: 'proPage.activate_title' })).toBeVisible();
  expect(screen.getByRole('link', { name: /proPage.read_terms/ })).toHaveAttribute(
    'href',
    'https://voicestudio.sh/terms',
  );
});

it('passes the selected user count and total to each website plan', () => {
  render(<ProPage />);
  const increase = screen.getAllByRole('button', { name: 'proPage.increase_users' });
  fireEvent.click(increase[0]);
  expect(screen.getByText('$198')).toBeVisible();
  expect(screen.getByRole('link', { name: /proPage.choose_yearly/ })).toHaveAttribute(
    'href',
    'https://voicestudio.sh/pro?plan=yearly&quantity=2',
  );
  fireEvent.click(increase[1]);
  expect(screen.getByText('$598')).toBeVisible();
  expect(screen.getByRole('link', { name: /proPage.choose_lifetime/ })).toHaveAttribute(
    'href',
    'https://voicestudio.sh/pro?plan=lifetime&quantity=2',
  );
});

it('activates an emailed licence and allows moving it to another device', async () => {
  render(<ProPage />);
  await waitFor(() => expect(status).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button', { name: 'proPage.activate_title' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'proPage.key_label' }), {
    target: { value: 'a1b2c3d4-e5f6-7890' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'proPage.activate' }));
  await waitFor(() => expect(activate).toHaveBeenCalledWith('a1b2c3d4-e5f6-7890'));
  await waitFor(() => expect(screen.getByText('proPage.active_title')).toBeVisible());
  fireEvent.click(screen.getByRole('button', { name: 'proPage.deactivate' }));
  await waitFor(() => expect(deactivate).toHaveBeenCalledOnce());
});

it('keeps the page usable when an older main process has no Pro handler', async () => {
  status.mockRejectedValueOnce(new Error("No handler registered for 'pro:status'"));
  render(<ProPage />);
  fireEvent.click(screen.getByRole('button', { name: 'proPage.activate_title' }));
  expect(await screen.findByText('proPage.not_configured')).toBeVisible();
});

it('recovers from rejected license IPC without leaving activation busy', async () => {
  activate.mockRejectedValueOnce(new Error('IPC unavailable'));
  render(<ProPage />);
  await waitFor(() => expect(status).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: 'proPage.activate_title' }));
  fireEvent.change(screen.getByLabelText('proPage.key_label'), {
    target: { value: 'test-license-123456' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'proPage.activate' }));
  await waitFor(() => expect(screen.getByText('proPage.error_offline')).toBeVisible());
  expect(screen.getByRole('button', { name: 'proPage.activate' })).not.toBeDisabled();
});

it('localizes fixed USD prices and quantity totals', () => {
  locale.current = 'de';
  render(<ProPage />);
  const usd = new Intl.NumberFormat('de', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  });
  expect(
    screen.getAllByText(usd.format(99), { exact: true, normalizer: (text) => text }),
  ).toHaveLength(2);
  fireEvent.click(screen.getAllByRole('button', { name: 'proPage.increase_users' })[0]);
  expect(
    screen.getByText(usd.format(198), { exact: true, normalizer: (text) => text }),
  ).toBeVisible();
});
