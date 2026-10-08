import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import type { Profile } from '@/lib/api/types';
import { queryKeys } from '@/lib/query';
import { cloneSettingsStore, patchCloneSettings } from '@/lib/store/clone-settings';

const mock = vi.hoisted(() => ({
  replace: vi.fn(),
  remove: vi.fn(),
  list: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
}));
vi.mock('@/lib/api/profiles', () => ({
  createCloneProfile: vi.fn(),
  deleteProfile: mock.remove,
  listProfiles: mock.list,
  replaceProfileAudio: mock.replace,
}));
vi.mock('./use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));
import { useDeleteProfile, useProfiles, useReplaceProfileAudio } from './use-profiles';
import { readDraft, writeDraft } from '@/features/design/design-draft';

vi.mock('sonner', () => ({ toast: { error: mock.error, success: mock.success } }));
vi.mock('@/lib/i18n-text', () => ({
  tr: (key: string, options?: { message?: string }) => `${key}: ${options?.message ?? ''}`,
}));

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
  vi.useRealTimers();
  onlineManager.setOnline(true);
  vi.clearAllMocks();
  patchCloneSettings({ selectedProfileId: null, refText: '' });
  localStorage.clear();
});

it.each(['same hook', 'separate hooks'])(
  'continues queued deletion confirmation after an earlier request stalls: %s',
  async (hookMode) => {
    const stalled = deferred<Profile[]>();
    mock.remove.mockRejectedValue(new Error('cleanup incomplete'));
    mock.list.mockReturnValueOnce(stalled.promise).mockResolvedValue([]);
    patchCloneSettings({ selectedProfileId: 'v2' });
    writeDraft({ ...readDraft(), profileId: 'v2' });
    const { client, result, wrapper } = setupDelete();
    const second =
      hookMode === 'separate hooks'
        ? renderHook(() => useDeleteProfile(), { wrapper }).result
        : result;
    vi.useFakeTimers();
    try {
      await act(async () => {
        await expect(result.current.mutateAsync('v1')).rejects.toThrow('cleanup incomplete');
        await expect(second.current.mutateAsync('v2')).rejects.toThrow('cleanup incomplete');
      });
      expect(mock.list).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(29_999);
      });
      expect(cloneSettingsStore.state.selectedProfileId).toBe('v2');
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(mock.list).toHaveBeenCalledTimes(2);
      expect(cloneSettingsStore.state.selectedProfileId).toBeNull();
      expect(readDraft().profileId).toBeNull();
      expect(client.getQueryData(queryKeys.profiles)).toEqual([]);

      // A cancelled transport that ignores abort cannot overwrite a newer list
      // or clear a voice whose absence the timed-out request never established.
      patchCloneSettings({ selectedProfileId: 'v1' });
      writeDraft({ ...readDraft(), profileId: 'v1' });
      await act(async () => {
        stalled.resolve([profile({ id: 'v2' })]);
      });
      expect(client.getQueryData(queryKeys.profiles)).toEqual([]);
      expect(cloneSettingsStore.state.selectedProfileId).toBe('v1');
      expect(readDraft().profileId).toBe('v1');
    } finally {
      client.clear();
    }
  },
);

it('does not expire queued confirmation while offline longer than its request deadline', async () => {
  const responses = deferred<void>();
  mock.remove.mockImplementation(async () => {
    await responses.promise;
    throw new Error('backend stopped after DELETE');
  });
  mock.list.mockResolvedValue([]);
  patchCloneSettings({ selectedProfileId: 'v1' });
  writeDraft({ ...readDraft(), profileId: 'v2' });
  const { client, result, wrapper } = setupDelete();
  const second = renderHook(() => useDeleteProfile(), { wrapper });
  vi.useFakeTimers();
  try {
    await act(async () => {
      const first = result.current.mutateAsync('v1').catch((error) => error);
      const next = second.result.current.mutateAsync('v2').catch((error) => error);
      await Promise.resolve();
      onlineManager.setOnline(false);
      responses.resolve();
      await Promise.all([first, next]);
    });
    expect(client.getQueryState(queryKeys.profiles)?.fetchStatus).toBe('paused');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(mock.list).not.toHaveBeenCalled();
    expect(cloneSettingsStore.state.selectedProfileId).toBe('v1');
    await act(async () => {
      onlineManager.setOnline(true);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mock.list).toHaveBeenCalledTimes(2);
    expect(cloneSettingsStore.state.selectedProfileId).toBeNull();
    expect(readDraft().profileId).toBeNull();
  } finally {
    client.clear();
  }
});

it('bounds confirmation when it joins a refresh already fetching after cancellation', async () => {
  const stalled = deferred<Profile[]>();
  mock.remove.mockRejectedValue(new Error('cleanup incomplete'));
  mock.list.mockReturnValueOnce(stalled.promise).mockResolvedValue([]);
  patchCloneSettings({ selectedProfileId: 'v2' });
  writeDraft({ ...readDraft(), profileId: 'v2' });
  const { client, result } = setupDelete();
  const cancelQueries = client.cancelQueries.bind(client);
  const cancel = vi.spyOn(client, 'cancelQueries').mockImplementationOnce((...args) => {
    const cancelled = cancelQueries(...args);
    void cancelled.then(() => {
      void client.fetchQuery({ queryKey: queryKeys.profiles, queryFn: mock.list }).catch(() => {});
    });
    return cancelled;
  });
  vi.useFakeTimers();
  try {
    await act(async () => {
      await expect(result.current.mutateAsync('v1')).rejects.toThrow('cleanup incomplete');
      await expect(result.current.mutateAsync('v2')).rejects.toThrow('cleanup incomplete');
    });
    expect(mock.list).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(mock.list).toHaveBeenCalledTimes(2);
    expect(cloneSettingsStore.state.selectedProfileId).toBeNull();
    expect(readDraft().profileId).toBeNull();
  } finally {
    client.clear();
    cancel.mockRestore();
  }
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

function setupDelete(seedProfiles = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (seedProfiles) {
    client.setQueryData<Profile[]>(queryKeys.profiles, [profile({}), profile({ id: 'v2' })]);
  }
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper, ...renderHook(() => useDeleteProfile(), { wrapper }) };
}

it('clears deleted clone and design references after cleanup fails and the list confirms deletion', async () => {
  const detail = 'The profile record was deleted, but asset cleanup is incomplete for: ref.wav.';
  mock.remove.mockRejectedValue(new Error(detail));
  mock.list.mockResolvedValue([profile({ id: 'v2' })]);
  patchCloneSettings({ selectedProfileId: 'v1' });
  writeDraft({ ...readDraft(), profileId: 'v1' });
  const { client, result } = setupDelete();

  await act(async () => {
    await expect(result.current.mutateAsync('v1')).rejects.toThrow(detail);
  });

  expect(cloneSettingsStore.state.selectedProfileId).toBeNull();
  expect(readDraft().profileId).toBeNull();
  expect(client.getQueryData<Profile[]>(queryKeys.profiles)?.map((p) => p.id)).toEqual(['v2']);
  expect(mock.error).toHaveBeenCalledWith(expect.stringContaining(detail));
  expect(mock.success).not.toHaveBeenCalled();
});

it.each(['rollback', 'unreachable'])(
  'preserves selected voices when deletion cannot be confirmed: %s',
  async (failure) => {
    mock.remove.mockRejectedValue(new Error('database commit failed'));
    if (failure === 'rollback') mock.list.mockResolvedValue([profile({}), profile({ id: 'v2' })]);
    else mock.list.mockRejectedValue(new Error('backend unavailable'));
    patchCloneSettings({ selectedProfileId: 'v1' });
    writeDraft({ ...readDraft(), profileId: 'v1' });
    const { client, result } = setupDelete();

    await act(async () => {
      await expect(result.current.mutateAsync('v1')).rejects.toThrow('database commit failed');
    });

    expect(cloneSettingsStore.state.selectedProfileId).toBe('v1');
    expect(readDraft().profileId).toBe('v1');
    expect(client.getQueryData<Profile[]>(queryKeys.profiles)?.map((p) => p.id)).toEqual([
      'v1',
      'v2',
    ]);
    expect(mock.error).toHaveBeenCalledWith(expect.stringContaining('database commit failed'));
  },
);

it('preserves another selected voice after a confirmed partial deletion', async () => {
  mock.remove.mockRejectedValue(new Error('cleanup incomplete'));
  mock.list.mockResolvedValue([profile({ id: 'v2' })]);
  patchCloneSettings({ selectedProfileId: 'v2' });
  writeDraft({ ...readDraft(), profileId: 'v2' });
  const { result } = setupDelete();

  await act(async () => {
    await expect(result.current.mutateAsync('v1')).rejects.toThrow('cleanup incomplete');
  });

  expect(cloneSettingsStore.state.selectedProfileId).toBe('v2');
  expect(readDraft().profileId).toBe('v2');
});

it('clears selected voices after an ordinary successful deletion', async () => {
  mock.remove.mockResolvedValue(undefined);
  patchCloneSettings({ selectedProfileId: 'v1' });
  writeDraft({ ...readDraft(), profileId: 'v1' });
  const { result } = setupDelete();

  await act(() => result.current.mutateAsync('v1'));

  expect(cloneSettingsStore.state.selectedProfileId).toBeNull();
  expect(readDraft().profileId).toBeNull();
  expect(mock.success).toHaveBeenCalled();
  expect(mock.error).not.toHaveBeenCalled();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

it('does not overwrite a newer query refresh with an older deletion-error list', async () => {
  const stale = deferred<Profile[]>();
  mock.remove.mockRejectedValue(new Error('cleanup incomplete'));
  mock.list.mockReturnValueOnce(stale.promise);
  patchCloneSettings({ selectedProfileId: 'v1' });
  writeDraft({ ...readDraft(), profileId: 'v1' });
  const { client, result } = setupDelete();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  renderHook(() => useProfiles(), { wrapper });
  let deletion!: Promise<unknown>;
  await act(async () => {
    deletion = result.current.mutateAsync('v1').catch((error) => error);
    await Promise.resolve();
  });
  await waitFor(() => expect(mock.list).toHaveBeenCalledTimes(1));
  const updated = profile({ id: 'v2', name: 'Updated voice' });
  const created = profile({ id: 'v3', name: 'New voice' });
  mock.list.mockResolvedValueOnce([updated, created]);
  await act(() => client.invalidateQueries({ queryKey: queryKeys.profiles }));
  expect(client.getQueryData<Profile[]>(queryKeys.profiles)).toEqual([updated, created]);
  await act(async () => {
    stale.resolve([profile({ id: 'v2' })]);
    await deletion;
  });
  expect(client.getQueryData<Profile[]>(queryKeys.profiles)).toEqual([updated, created]);
  expect(cloneSettingsStore.state.selectedProfileId).toBeNull();
  expect(readDraft().profileId).toBeNull();
});

it('cancels an older profile query before confirming deletion after an error', async () => {
  const stale = deferred<Profile[]>();
  mock.list.mockReturnValueOnce(stale.promise).mockResolvedValueOnce([profile({ id: 'v2' })]);
  mock.remove.mockRejectedValue(new Error('cleanup incomplete'));
  patchCloneSettings({ selectedProfileId: 'v1' });
  writeDraft({ ...readDraft(), profileId: 'v1' });
  const { client, result } = setupDelete();
  const older = client
    .fetchQuery({ queryKey: queryKeys.profiles, queryFn: mock.list, staleTime: 0 })
    .catch((error) => error);
  await act(async () => {
    await expect(result.current.mutateAsync('v1')).rejects.toThrow('cleanup incomplete');
  });
  expect(cloneSettingsStore.state.selectedProfileId).toBeNull();
  expect(readDraft().profileId).toBeNull();
  await act(async () => {
    stale.resolve([profile({}), profile({ id: 'v2' })]);
    await older;
  });
  expect(client.getQueryData<Profile[]>(queryKeys.profiles)?.map((p) => p.id)).toEqual(['v2']);
});

it('settles a deletion error while the native backend is offline and preserves selections', async () => {
  mock.remove.mockImplementation(async () => {
    onlineManager.setOnline(false);
    throw new Error('backend offline');
  });
  mock.list.mockRejectedValue(new Error('backend offline'));
  patchCloneSettings({ selectedProfileId: 'v1' });
  writeDraft({ ...readDraft(), profileId: 'v1' });
  const { result } = setupDelete();
  let deletion!: Promise<unknown>;
  try {
    await act(async () => {
      deletion = result.current.mutateAsync('v1').catch((error) => error);
      await Promise.resolve();
    });
    // Must settle while offline; a hang surfaces as the test timeout rather
    // than a wall-clock race that a slow runner could lose.
    await deletion;
    await waitFor(() => expect(result.current.isPending).toBe(false));
    expect(cloneSettingsStore.state.selectedProfileId).toBe('v1');
    expect(readDraft().profileId).toBe('v1');
    expect(mock.list).not.toHaveBeenCalled();
  } finally {
    onlineManager.setOnline(true);
    await act(async () => {
      await deletion;
    });
  }
});

it.each(['deleted', 'rollback', 'unreachable'])(
  'reconciles a deletion error after backend readiness without assuming deletion: %s',
  async (serverOutcome) => {
    mock.remove.mockImplementation(async () => {
      onlineManager.setOnline(false);
      throw new Error('backend stopped after DELETE');
    });
    if (serverOutcome === 'deleted') mock.list.mockResolvedValue([profile({ id: 'v2' })]);
    else if (serverOutcome === 'rollback') {
      mock.list.mockResolvedValue([profile({}), profile({ id: 'v2' })]);
    } else mock.list.mockRejectedValue(new Error('backend still unavailable'));
    patchCloneSettings({ selectedProfileId: 'v1' });
    writeDraft({ ...readDraft(), profileId: 'v1' });
    const { client, result } = setupDelete();
    try {
      await act(async () => {
        await expect(result.current.mutateAsync('v1')).rejects.toThrow(
          'backend stopped after DELETE',
        );
      });
      await waitFor(() => expect(result.current.isPending).toBe(false));
      expect(mock.list).not.toHaveBeenCalled();
      expect(cloneSettingsStore.state.selectedProfileId).toBe('v1');
      expect(readDraft().profileId).toBe('v1');

      await act(async () => {
        onlineManager.setOnline(true);
      });
      await waitFor(() => expect(mock.list).toHaveBeenCalledTimes(1));
      await waitFor(() =>
        expect(client.getQueryState(queryKeys.profiles)?.fetchStatus).toBe('idle'),
      );
      const expectedSelection = serverOutcome === 'deleted' ? null : 'v1';
      expect(cloneSettingsStore.state.selectedProfileId).toBe(expectedSelection);
      expect(readDraft().profileId).toBe(expectedSelection);
      expect(client.getQueryData<Profile[]>(queryKeys.profiles)?.map((p) => p.id)).toEqual(
        serverOutcome === 'deleted' ? ['v2'] : ['v1', 'v2'],
      );
    } finally {
      client.clear();
    }
  },
);

it.each(['paused', 'fetching'])(
  'cancels managed confirmation on query-client clear without applying a stale result after remount: %s',
  async (confirmationState) => {
    const stale = deferred<Profile[]>();
    mock.remove.mockImplementation(async () => {
      if (confirmationState === 'paused') onlineManager.setOnline(false);
      throw new Error('cleanup incomplete');
    });
    mock.list.mockReturnValue(stale.promise);
    patchCloneSettings({ selectedProfileId: 'v1' });
    writeDraft({ ...readDraft(), profileId: 'v1' });
    const { client, result, unmount } = setupDelete();
    await act(async () => {
      await expect(result.current.mutateAsync('v1')).rejects.toThrow('cleanup incomplete');
    });
    await waitFor(() =>
      expect(client.getQueryState(queryKeys.profiles)?.fetchStatus).toBe(confirmationState),
    );
    unmount();
    client.clear();
    const currentProfiles = [profile({}), profile({ id: 'v2', name: 'Fresh cached voice' })];
    client.setQueryData(queryKeys.profiles, currentProfiles);
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const remounted = renderHook(() => useProfiles(), { wrapper });
    try {
      await act(async () => {
        onlineManager.setOnline(true);
        stale.resolve([profile({ id: 'v2', name: 'Stale deletion result' })]);
      });
      expect(mock.list).toHaveBeenCalledTimes(confirmationState === 'paused' ? 0 : 1);
      expect(client.getQueryData(queryKeys.profiles)).toEqual(currentProfiles);
      expect(cloneSettingsStore.state.selectedProfileId).toBe('v1');
      expect(readDraft().profileId).toBe('v1');
    } finally {
      remounted.unmount();
      client.clear();
    }
  },
);

it.each(['same-hook', 'separate-hooks'])(
  'confirms both failed batch deletions without losing the first selected voice: %s',
  async (hookLayout) => {
    const firstConfirmation = deferred<Profile[]>();
    mock.remove.mockRejectedValue(new Error('cleanup incomplete'));
    mock.list.mockReturnValueOnce(firstConfirmation.promise).mockResolvedValueOnce([]);
    patchCloneSettings({ selectedProfileId: 'v1' });
    writeDraft({ ...readDraft(), profileId: 'v2' });
    const { client, result, wrapper } = setupDelete();
    const second =
      hookLayout === 'separate-hooks'
        ? renderHook(() => useDeleteProfile(), { wrapper }).result
        : result;
    try {
      await act(async () => {
        await expect(result.current.mutateAsync('v1')).rejects.toThrow('cleanup incomplete');
      });
      await waitFor(() => expect(mock.list).toHaveBeenCalledTimes(1));
      await act(async () => {
        await expect(second.current.mutateAsync('v2')).rejects.toThrow('cleanup incomplete');
      });
      await act(async () => {
        firstConfirmation.resolve([profile({ id: 'v2' })]);
      });
      await waitFor(() => expect(mock.list).toHaveBeenCalledTimes(2));
      await waitFor(() =>
        expect(client.getQueryState(queryKeys.profiles)?.fetchStatus).toBe('idle'),
      );
      expect(client.getQueryData<Profile[]>(queryKeys.profiles)).toEqual([]);
      expect(cloneSettingsStore.state.selectedProfileId).toBeNull();
      expect(readDraft().profileId).toBeNull();
    } finally {
      client.clear();
    }
  },
);

it('settles two in-flight DELETE errors offline and confirms both after reconnect across hooks', async () => {
  const responses = deferred<void>();
  mock.remove.mockImplementation(async () => {
    await responses.promise;
    throw new Error('backend stopped after DELETE');
  });
  mock.list.mockResolvedValue([]);
  patchCloneSettings({ selectedProfileId: 'v1' });
  writeDraft({ ...readDraft(), profileId: 'v2' });
  const { client, result, wrapper } = setupDelete();
  const second = renderHook(() => useDeleteProfile(), { wrapper });
  let firstError!: Promise<unknown>;
  let secondError!: Promise<unknown>;
  try {
    await act(async () => {
      firstError = result.current.mutateAsync('v1').catch((error) => error);
      secondError = second.result.current.mutateAsync('v2').catch((error) => error);
    });
    await waitFor(() => expect(mock.remove).toHaveBeenCalledTimes(2));
    await act(async () => {
      onlineManager.setOnline(false);
      responses.resolve();
      await Promise.all([firstError, secondError]);
    });
    await waitFor(() => {
      expect(result.current.isPending).toBe(false);
      expect(second.result.current.isPending).toBe(false);
    });
    expect(mock.list).not.toHaveBeenCalled();
    expect(cloneSettingsStore.state.selectedProfileId).toBe('v1');
    expect(readDraft().profileId).toBe('v2');

    await act(async () => {
      onlineManager.setOnline(true);
    });
    await waitFor(() => expect(mock.list).toHaveBeenCalledTimes(2));
    await waitFor(() => {
      expect(cloneSettingsStore.state.selectedProfileId).toBeNull();
      expect(readDraft().profileId).toBeNull();
    });
    expect(client.getQueryData(queryKeys.profiles)).toEqual([]);
  } finally {
    client.clear();
  }
});

it.each([true, false])(
  'discards a queued confirmation on cache clear while the first is paused (initial query: %s)',
  async (seedProfiles) => {
    const responses = deferred<void>();
    mock.remove.mockImplementation(async () => {
      await responses.promise;
      throw new Error('backend stopped after DELETE');
    });
    mock.list.mockResolvedValue([]);
    patchCloneSettings({ selectedProfileId: 'v1' });
    writeDraft({ ...readDraft(), profileId: 'v2' });
    const { client, result, wrapper, unmount } = setupDelete(seedProfiles);
    const second = renderHook(() => useDeleteProfile(), { wrapper });
    let firstError!: Promise<unknown>;
    let secondError!: Promise<unknown>;
    await act(async () => {
      firstError = result.current.mutateAsync('v1').catch((error) => error);
      secondError = second.result.current.mutateAsync('v2').catch((error) => error);
    });
    await waitFor(() => expect(mock.remove).toHaveBeenCalledTimes(2));
    await act(async () => {
      onlineManager.setOnline(false);
      responses.resolve();
      await Promise.all([firstError, secondError]);
    });
    await waitFor(() =>
      expect(client.getQueryState(queryKeys.profiles)?.fetchStatus).toBe('paused'),
    );
    unmount();
    second.unmount();
    client.clear();
    const currentProfiles = [profile({}), profile({ id: 'v2' })];
    client.setQueryData(queryKeys.profiles, currentProfiles);
    const remounted = renderHook(() => useProfiles(), { wrapper });
    try {
      await act(async () => {
        onlineManager.setOnline(true);
      });
      expect(mock.list).not.toHaveBeenCalled();
      expect(client.getQueryData(queryKeys.profiles)).toEqual(currentProfiles);
      expect(cloneSettingsStore.state.selectedProfileId).toBe('v1');
      expect(readDraft().profileId).toBe('v2');
    } finally {
      remounted.unmount();
      client.clear();
    }
  },
);

it('confirms deletion when a live query client has no loaded profiles query', async () => {
  mock.remove.mockRejectedValue(new Error('cleanup incomplete'));
  mock.list.mockResolvedValue([]);
  patchCloneSettings({ selectedProfileId: 'v1' });
  writeDraft({ ...readDraft(), profileId: 'v1' });
  const { client, result } = setupDelete(false);
  try {
    expect(client.getQueryState(queryKeys.profiles)).toBeUndefined();
    await act(async () => {
      await expect(result.current.mutateAsync('v1')).rejects.toThrow('cleanup incomplete');
    });
    await waitFor(() => expect(mock.list).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(cloneSettingsStore.state.selectedProfileId).toBeNull();
      expect(readDraft().profileId).toBeNull();
    });
  } finally {
    client.clear();
  }
});

it('uses normal query-client defaults and identity without fetching while offline', async () => {
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 60_000,
        queryKeyHashFn: (key) => `custom:${JSON.stringify(key)}`,
      },
    },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  mock.remove.mockImplementation(async () => {
    onlineManager.setOnline(false);
    throw new Error('backend offline');
  });
  mock.list.mockResolvedValue([]);
  patchCloneSettings({ selectedProfileId: 'v1' });
  writeDraft({ ...readDraft(), profileId: 'v1' });
  const { result } = renderHook(() => useDeleteProfile(), { wrapper });
  try {
    await act(async () => {
      await expect(result.current.mutateAsync('v1')).rejects.toThrow('backend offline');
    });
    await waitFor(() =>
      expect(client.getQueryState(queryKeys.profiles)?.fetchStatus).toBe('paused'),
    );
    expect(client.getQueryCache().getAll()).toHaveLength(1);
    expect(client.getQueryCache().find({ queryKey: queryKeys.profiles })?.queryHash).toBe(
      `custom:${JSON.stringify(queryKeys.profiles)}`,
    );
    expect(mock.list).not.toHaveBeenCalled();

    await act(async () => {
      onlineManager.setOnline(true);
    });
    await waitFor(() => expect(mock.list).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(cloneSettingsStore.state.selectedProfileId).toBeNull();
      expect(readDraft().profileId).toBeNull();
    });
    expect(client.getQueryCache().getAll()).toHaveLength(1);
    expect(client.getQueryData(queryKeys.profiles)).toEqual([]);
  } finally {
    client.clear();
  }
});
