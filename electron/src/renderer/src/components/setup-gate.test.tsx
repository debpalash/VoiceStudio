import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({ ready: true, preflight: { ok: false } }));
vi.mock('@/lib/api/client', () => ({
  apiJson: (path: string) =>
    Promise.resolve(
      path.endsWith('/setup/preflight')
        ? mock.preflight
        : {
            models_ready: mock.ready,
            missing: mock.ready ? [] : [{ label: 'Required voice model' }],
          },
    ),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/features/settings/system-preflight', () => ({
  SystemPreflight: () => <div>Preflight</div>,
}));
vi.mock('@/features/settings/model-library', () => ({
  ModelLibrary: () => <div>Models</div>,
  PerformanceModelPacks: () => <div>Model packs</div>,
}));
vi.mock('@/features/settings/model-settings', () => ({
  ModelSettings: () => <div>Engine settings</div>,
  modelFamilies: ['tts'],
}));
vi.mock('@/features/settings/mirror-settings', () => ({ MirrorSettings: () => null }));
vi.mock('@/features/settings/media-tools', () => ({
  SetupMediaEngine: () => null,
  MediaTools: () => null,
}));
vi.mock('@/features/settings/privacy-settings', () => ({
  PrivacySettings: () => <div>Privacy</div>,
}));
vi.mock('./analytics-consent', () => ({
  AnalyticsConsent: ({ onRequirementChange }: { onRequirementChange(value: boolean): void }) => (
    <button onClick={() => onRequirementChange(false)}>Choose privacy</button>
  ),
}));
vi.mock('@/features/settings/shortcut-settings', () => ({
  ShortcutSettings: () => <div>Shortcuts</div>,
}));
import { SetupGate } from './setup-gate';
beforeEach(() => localStorage.clear());
afterEach(() => {
  cleanup();
  mock.ready = true;
  mock.preflight = { ok: false };
});
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SetupGate>
        <div>Studio</div>
      </SetupGate>
    </QueryClientProvider>,
  );
  return client;
}
it('bypasses onboarding for a backend with required models present', async () => {
  localStorage.setItem('voicestudio.setup.complete.v1', '1');
  mount();
  await screen.findByText('Studio');
  expect(screen.queryByText('Preflight')).not.toBeInTheDocument();
});
it('requires passing preflight and installed models before completion', async () => {
  mock.ready = false;
  const client = mount();
  await screen.findByText('Preflight');
  // A partial response from an older or interrupted backend must fail closed
  // without blanking the entire first-run experience.
  await waitFor(() => expect(client.getQueryData(['setup-preflight'])).toEqual({ ok: false }));
  expect(screen.getByRole('button', { name: 'setup.continue_ok' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '4.setup.enter_studio' })).toBeDisabled();
  client.setQueryData(['setup-preflight'], { ok: true, checks: [] });
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'setup.continue_ok' })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'setup.continue_ok' }));
  await screen.findByText('Model packs');
  expect(screen.queryByText('Models')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'dub.advanced' }));
  expect(screen.getByText('Models')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'dub.advanced' }));
  expect(screen.getByRole('button', { name: 'setup.continue_ok' })).toBeDisabled();
  mock.ready = true;
  await client.invalidateQueries({ queryKey: ['setup-status'] });
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'setup.continue_ok' })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'setup.continue_ok' }));
  await screen.findByRole('button', { name: 'Choose privacy' });
  expect(screen.queryByText('Privacy')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Choose privacy' }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'setup.continue_ok' })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'setup.continue_ok' }));
  await screen.findByText('setup.ready_desc');
  expect(screen.queryByText('Shortcuts')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /demo.dictation_title/ }));
  expect(screen.getByText('Shortcuts')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /demo.dictation_title/ }));
  expect(screen.queryByText('Shortcuts')).not.toBeInTheDocument();
  let finishCatalogue!: () => void;
  const catalogueReady = new Promise<void>((resolve) => {
    finishCatalogue = resolve;
  });
  const invalidate = vi
    .spyOn(client, 'invalidateQueries')
    .mockImplementation((filters) =>
      (filters?.queryKey as string[] | undefined)?.[0] === 'model-catalogue'
        ? catalogueReady
        : Promise.resolve(),
    );
  const handoff = vi.fn();
  window.addEventListener('voicestudio:first-sound', handoff);
  fireEvent.click(screen.getByRole('button', { name: 'setup.enter_studio' }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'setup.enter_studio' })).toBeDisabled(),
  );
  expect(screen.queryByText('Studio')).not.toBeInTheDocument();
  expect(handoff).not.toHaveBeenCalled();
  finishCatalogue();
  await screen.findByText('Studio');
  await waitFor(() => expect(handoff).toHaveBeenCalledOnce());
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['model-catalogue'] });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['engines'] });
  window.removeEventListener('voicestudio:first-sound', handoff);
});
