import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import type { Profile } from '@/lib/api/types';
import { queryKeys } from '@/lib/query';
import { cloneSettingsStore, patchCloneSettings } from '@/lib/store/clone-settings';

const mock = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock('@/lib/api/profiles', () => ({
  createCloneProfile: vi.fn(),
  deleteProfile: vi.fn(),
  listProfiles: vi.fn(),
  replaceProfileAudio: mock.replace,
}));
vi.mock('./use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));
import { useReplaceProfileAudio } from './use-profiles';

const profile = (patch: Partial<Profile>): Profile =>
  ({ id: 'v1', name: 'Voice', kind: 'clone', ref_text: 'old words', ...patch }) as Profile;

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData<Profile[]>(queryKeys.profiles, [
    profile({ audio_url: '/profiles/v1/audio?v=1' }),
    profile({ id: 'v2', name: 'Other' }),
  ]);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, ...renderHook(() => useReplaceProfileAudio(), { wrapper }) };
}

afterEach(() => {
  vi.clearAllMocks();
  patchCloneSettings({ selectedProfileId: null, refText: '' });
});

it('writes the replaced clip back into the cached profile and the selected transcript', async () => {
  const updated = profile({ ref_text: 'new words', audio_url: '/profiles/v1/audio?v=2' });
  mock.replace.mockResolvedValue(updated);
  patchCloneSettings({ selectedProfileId: 'v1', refText: 'old words' });
  const { client, result } = setup();
  const clip = new File(['audio'], 'take.wav', { type: 'audio/wav' });

  await act(() => result.current.mutateAsync({ id: 'v1', refAudio: clip, refText: '' }));

  expect(mock.replace).toHaveBeenCalledWith('v1', { refAudio: clip, refText: '' });
  const cached = client.getQueryData<Profile[]>(queryKeys.profiles)!;
  expect(cached[0]).toEqual(updated);
  expect(cached[1].id).toBe('v2');
  expect(cloneSettingsStore.state.refText).toBe('new words');
});

it('leaves the composer alone when another voice is selected', async () => {
  mock.replace.mockResolvedValue(profile({ ref_text: 'new words' }));
  patchCloneSettings({ selectedProfileId: 'v2', refText: 'keep me' });
  const { result } = setup();

  await act(() => result.current.mutateAsync({ id: 'v1', refAudio: new Blob(['a']) }));

  expect(cloneSettingsStore.state.refText).toBe('keep me');
});
