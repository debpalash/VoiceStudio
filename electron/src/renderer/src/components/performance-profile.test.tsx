import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ global: 'max' as string }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'en', dir: () => 'ltr' },
  }),
}));
vi.mock('@/lib/api/client', () => ({ apiJson: vi.fn(async () => []), describeError: String }));
vi.mock('@/lib/app-activity', () => ({ useAppActivities: () => ({}) }));
vi.mock('@/hooks/use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));
vi.mock('@shared/utils/backendStage', () => ({ isBackendReachable: () => true }));
vi.mock('@/hooks/use-engines', () => ({
  useEngines: () => ({ data: undefined }),
  engineFamilyState: () => null,
}));
vi.mock('@/hooks/use-dictation-selection', () => ({ useDictationSelection: () => ({}) }));
vi.mock('@/components/live-device-usage', () => ({ LiveDeviceUsage: () => null }));
vi.mock('@/hooks/use-performance-profile', () => ({
  performanceTiers: ['fast', 'balanced', 'quality', 'max'],
  usePerformanceProfile: () => ({
    data: {
      global: state.global,
      effective: {},
      plan: {
        status: 'fits',
        max_status: 'fits',
        resolved: 'quality',
        families: Object.fromEntries(
          ['tts', 'asr', 'translation', 'dictation', 'diarisation'].map((name) => [
            name,
            { reason: 'fits' },
          ]),
        ),
        hardware: { cpu_threads: 8 },
      },
    },
    isSaving: false,
    isError: false,
    setTier: vi.fn(),
    refetch: vi.fn(),
  }),
}));

import { PerformanceProfile } from './performance-profile';

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PerformanceProfile extra={<span />} showHardware={false} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  state.global = 'max';
});

it('names the sidebar control and shows the current level beside its slider', () => {
  mount();
  expect(screen.getByText('performanceProfile.title')).toBeVisible();
  const slider = screen.getByRole('slider', { name: 'performanceProfile.title' });
  expect(slider).toHaveAttribute('aria-valuetext', 'performanceProfile.max');
  const level = document.querySelector('[data-slot="performance-trigger"]');
  expect(level).toHaveTextContent('performanceProfile.max');
});

it('explains Auto and shows the level Auto resolved to', () => {
  state.global = 'auto';
  mount();
  const auto = screen.getByRole('button', { name: 'performanceProfile.auto' });
  expect(auto).toHaveAttribute('aria-pressed', 'true');
  expect(auto).toHaveAccessibleDescription('performanceProfile.autoHint');
  const level = document.querySelector('[data-slot="performance-trigger"]');
  expect(level).toHaveTextContent('performanceProfile.quality');
});
