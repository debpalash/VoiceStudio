import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  recorded: null as null | ((file: File) => void),
  success: vi.fn(),
}));
vi.mock('@/lib/api/client', () => ({
  apiJson: mocks.api,
  describeError: (error: unknown) => (error instanceof Error ? error.message : String(error)),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('sonner', () => ({ toast: { success: mocks.success } }));
vi.mock('@/components/recording-inputs', () => ({ RecordingInputs: () => null }));
vi.mock('@/hooks/use-recording', () => ({
  useRecording: (callback: (file: File) => void) => {
    mocks.recorded = callback;
    return {
      isRecording: false,
      isStarting: false,
      isCleaning: false,
      start: vi.fn(),
      stop: vi.fn(),
    };
  },
}));
import { ProfileConsent } from './profile-consent';
import type { Profile } from '@/lib/api/types';
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function mount(verified = false, locked = false) {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ProfileConsent
        profile={{ id: 'p/1', verified_own_voice: verified, is_locked: locked } as Profile}
      />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText('voice_profile.consent_title'));
}
it('submits the recorded clip and localized statement only after recording', async () => {
  mocks.api.mockResolvedValue({});
  mount();
  expect(mocks.api).not.toHaveBeenCalled();
  const file = new File(['clip'], 'consent.wav');
  mocks.recorded!(file);
  await waitFor(() => expect(mocks.api).toHaveBeenCalled());
  const [path, options] = mocks.api.mock.calls[0];
  expect(path).toBe('/profiles/p%2F1/consent');
  expect(options.method).toBe('POST');
  expect(options.body.get('consent_audio')).toBe(file);
  expect(options.body.get('consent_text')).toBe('voice_profile.consent_statement');
  expect(mocks.success).toHaveBeenCalledWith('voice_profile.consent_saved');
});
it('requires explicit confirmation to revoke consent and supports cancelling', async () => {
  mocks.api.mockResolvedValue({});
  mount(true);
  fireEvent.click(screen.getByRole('button', { name: 'voice_profile.consent_revoke' }));
  expect(mocks.api).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'common.cancel' }));
  expect(mocks.api).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'voice_profile.consent_revoke' }));
  fireEvent.click(screen.getByRole('button', { name: 'common.confirm' }));
  await waitFor(() =>
    expect(mocks.api).toHaveBeenCalledWith(
      '/profiles/p%2F1/consent',
      expect.objectContaining({ method: 'DELETE' }),
    ),
  );
  expect(mocks.success).toHaveBeenCalledWith('voice_profile.consent_revoked');
});
it('confirms a successful unlock with the same feedback as the Tauri profile', async () => {
  mocks.api.mockResolvedValue({});
  mount(true, true);
  fireEvent.click(screen.getByRole('button', { name: 'voice_profile.unlock' }));
  fireEvent.click(screen.getByRole('button', { name: 'common.confirm' }));
  await waitFor(() => expect(mocks.success).toHaveBeenCalledWith('voice_profile.unlocked'));
});
it('unlocks through the dedicated endpoint and retains retry controls on failure', async () => {
  mocks.api.mockRejectedValue(new Error('offline'));
  mount(true, true);
  fireEvent.click(screen.getByRole('button', { name: 'voice_profile.unlock' }));
  fireEvent.click(screen.getByRole('button', { name: 'common.confirm' }));
  await screen.findByRole('alert');
  expect(mocks.api).toHaveBeenCalledWith(
    '/profiles/p%2F1/unlock',
    expect.objectContaining({ method: 'POST' }),
  );
  expect(screen.getByRole('button', { name: 'common.confirm' })).toBeEnabled();
});

function mountWith(profile: Partial<Profile>) {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ProfileConsent profile={{ id: 'p/1', ...profile } as Profile} />
    </QueryClientProvider>,
  );
}

it.each([
  ['voice_profile.attest_own_voice', 'own_voice'],
  ['voice_profile.attest_permission', 'speaker_permission'],
])('%s records the consent basis needed before the voice can sync', async (label, basis) => {
  mocks.api.mockResolvedValue({});
  mountWith({});
  fireEvent.click(screen.getByText(label));
  await waitFor(() => expect(mocks.api).toHaveBeenCalled());
  const [path, init] = mocks.api.mock.calls[0];
  expect(path).toBe('/profiles/p%2F1/attestation');
  expect(init.method).toBe('POST');
  expect(JSON.parse(init.body)).toEqual({ basis });
});

it('an attested voice shows its basis and can clear it', async () => {
  mocks.api.mockResolvedValue({});
  mountWith({ consent_basis: 'speaker_permission', consent_attested_at: '2026-10-16T09:00:00Z' });
  expect(screen.getByText('voice_profile.attested_permission')).toBeInTheDocument();
  fireEvent.click(screen.getByText('voice_profile.attest_clear'));
  await waitFor(() => expect(mocks.api).toHaveBeenCalled());
  expect(mocks.api.mock.calls[0][0]).toBe('/profiles/p%2F1/attestation');
  expect(mocks.api.mock.calls[0][1].method).toBe('DELETE');
});
