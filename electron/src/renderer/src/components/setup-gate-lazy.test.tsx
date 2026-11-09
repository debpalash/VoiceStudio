import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

// #3924: the setup wizard's settings panels must stay out of the startup
// bundle. A static import would evaluate the panel module when SetupGate loads;
// the lazy split only evaluates it when the wizard is actually shown.
const loaded = vi.hoisted(() => ({ panels: false }));
vi.mock('@/features/settings/model-library', () => {
  loaded.panels = true;
  return { ModelLibrary: () => null, PerformanceModelPacks: () => null };
});
vi.mock('@/features/settings/system-preflight', () => ({
  SystemPreflight: () => <div>Preflight</div>,
}));
vi.mock('@/features/settings/media-tools', () => ({
  SetupMediaEngine: () => null,
  MediaTools: () => null,
}));
vi.mock('@/features/settings/model-settings', () => ({ ModelSettings: () => null }));
vi.mock('@/features/settings/mirror-settings', () => ({ MirrorSettings: () => null }));
vi.mock('@/features/settings/privacy-settings', () => ({ PrivacySettings: () => null }));
vi.mock('@/features/settings/shortcut-settings', () => ({ ShortcutSettings: () => null }));
vi.mock('./analytics-consent', () => ({ AnalyticsConsent: () => null }));
vi.mock('./account-consent', () => ({ AccountConsent: () => null }));
vi.mock('@/lib/api/client', () => ({
  apiJson: (path: string) =>
    Promise.resolve(
      path.endsWith('/setup/preflight') ? { ok: false } : { models_ready: true, missing: [] },
    ),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import { SetupGate } from './setup-gate';

beforeEach(() => localStorage.clear());
afterEach(cleanup);

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SetupGate>
        <div>Studio</div>
      </SetupGate>
    </QueryClientProvider>,
  );
}

it('opens the studio without loading the setup wizard for a returning user', async () => {
  localStorage.setItem('voicestudio.setup.complete.v1', '1');
  mount();
  await screen.findByText('Studio');
  expect(loaded.panels).toBe(false);
});

it('loads the setup wizard on a first run', async () => {
  mount();
  await screen.findByText('Preflight');
  expect(loaded.panels).toBe(true);
});
