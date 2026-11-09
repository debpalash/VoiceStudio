import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const locale = vi.hoisted(() => ({ current: 'en' }));
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

it('shows pricing followed by Pro features without licence activation', () => {
  render(<ProPage />);
  expect(screen.queryByText('$9')).not.toBeInTheDocument();
  expect(screen.getAllByText('$99')).toHaveLength(2);
  expect(screen.getAllByText('$199')).toHaveLength(2);
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
  expect(screen.queryByRole('button', { name: 'proPage.activate_title' })).not.toBeInTheDocument();
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
  expect(screen.getByText('$398')).toBeVisible();
  expect(screen.getByRole('link', { name: /proPage.choose_lifetime/ })).toHaveAttribute(
    'href',
    'https://voicestudio.sh/pro?plan=lifetime&quantity=2',
  );
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
