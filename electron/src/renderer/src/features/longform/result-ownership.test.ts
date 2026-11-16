import { createElement } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { DurableLongformRecord } from '@shared/utils/indexedDbLongformStore';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { BackendStatus } from '@/hooks/use-backend-status';

const fixture = vi.hoisted(() => ({
  status: {
    stage: 'ready',
    baseUrl: 'https://remote.fixture.invalid',
    remote: true,
    port: 3900,
    managed: false,
    elapsedMs: 0,
    logTail: [],
  } as BackendStatus,
  listeners: new Set<(status: BackendStatus) => void>(),
  fetch: vi.fn(),
}));
vi.mock('@/lib/api/client', async (original) => ({
  ...(await original<object>()),
  apiFetch: fixture.fetch,
}));
const remote = (baseUrl = 'https://remote.fixture.invalid'): BackendStatus => ({
  ...fixture.status,
  stage: 'ready',
  baseUrl,
  remote: true,
});
const local = (port = 3900): BackendStatus => ({
  ...fixture.status,
  runtimeStorage: undefined,
  stage: 'ready',
  baseUrl: `http://127.0.0.1:${port}`,
  port,
  remote: false,
});
const publish = (status: BackendStatus) => {
  fixture.status = status;
  for (const listener of fixture.listeners) listener(status);
};
const events = (output: string) =>
  new Response(
    [
      { type: 'started', chapters: 1 },
      { type: 'chapter', index: 0, title: 'Fixture', duration_ms: 100 },
      { type: 'done', output },
    ]
      .map((event) => `data: ${JSON.stringify(event)}\n\n`)
      .join(''),
  );
const loaded = new Set<typeof import('./longform-session')>();
async function load() {
  const session = await import('./longform-session');
  const backend = await import('@/hooks/use-backend-status');
  backend.subscribeBackendStatus(() => {});
  await Promise.resolve();
  await Promise.resolve();
  loaded.add(session);
  return session;
}
beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
  fixture.listeners.clear();
  fixture.fetch.mockReset();
  fixture.status = remote();
  Object.defineProperty(window, 'voicestudio', {
    configurable: true,
    value: {
      backend: {
        getStatus: async () => fixture.status,
        onStatus: (listener: (status: BackendStatus) => void) => {
          fixture.listeners.add(listener);
          return () => fixture.listeners.delete(listener);
        },
      },
    },
  });
});
afterEach(() => {
  cleanup();
  for (const session of loaded) session.clearLongformDraftForReset();
  loaded.clear();
  fixture.listeners.clear();
  vi.restoreAllMocks();
});
it.each(['stories', 'audiobook'] as const)(
  'retains %s editing data without showing another backend’s completed media',
  async (mode) => {
    const session = await load();
    session.editLongform(mode, {
      script: 'Neutral fixture.',
      lines: [{ id: 'line', text: 'Neutral fixture.', profileId: null }],
    });
    fixture.fetch.mockResolvedValue(events('remote.m4b'));
    await session.renderLongform(mode);
    session.flushLongformSessionPersistence();
    expect(session.longformSession.state.drafts[mode].output).toBe('remote.m4b');
    publish(local());
    expect(session.longformSession.state.drafts[mode].output).toBe('');
    expect(session.longformSession.state.drafts[mode].lines[0].text).toBe('Neutral fixture.');
    publish(remote());
    expect(session.longformSession.state.drafts[mode].output).toBe('remote.m4b');
  },
);
it('restores a same-backend completed result across reload', async () => {
  let session = await load();
  fixture.fetch.mockResolvedValue(events('remote.m4b'));
  await session.renderLongform('audiobook');
  session.flushLongformSessionPersistence();
  fixture.listeners.clear();
  vi.resetModules();
  session = await load();
  expect(session.longformSession.state.drafts.audiobook.output).toBe('remote.m4b');
});

it('keeps local ownership across port churn and separates data roots', async () => {
  fixture.status = local();
  const session = await load();
  fixture.fetch.mockResolvedValue(events('local.m4b'));
  await session.renderLongform('stories');
  publish(local(3911));
  expect(session.longformSession.state.drafts.stories.output).toBe('local.m4b');
  publish({ ...local(3911), runtimeStorage: { data: '/other', models: '/models', temp: '/tmp' } });
  expect(session.longformSession.state.drafts.stories.output).toBe('');
  publish(local(3912));
  expect(session.longformSession.state.drafts.stories.output).toBe('local.m4b');
});
it('preserves both owners and normalizes equivalent remote endpoints', async () => {
  const session = await load();
  fixture.fetch.mockResolvedValue(events('remote.m4b'));
  await session.renderLongform('stories');
  publish(local());
  fixture.fetch.mockResolvedValue(events('local.m4b'));
  await session.renderLongform('stories');
  publish(remote('https://REMOTE.fixture.invalid:443/'));
  expect(session.longformSession.state.drafts.stories.output).toBe('remote.m4b');
  publish(local());
  expect(session.longformSession.state.drafts.stories.output).toBe('local.m4b');
});
it('does not publish or persist unchanged-connection heartbeats', async () => {
  const session = await load();
  fixture.fetch.mockResolvedValue(events('remote.m4b'));
  await session.renderLongform('stories');
  session.flushLongformSessionPersistence();
  const previous = session.longformSession.state;
  const writes = vi.spyOn(Storage.prototype, 'setItem');
  publish({ ...fixture.status, elapsedMs: 1000, logTail: ['heartbeat'] });
  session.flushLongformSessionPersistence();
  expect(session.longformSession.state).toBe(previous);
  expect(writes).not.toHaveBeenCalled();
});
function seedLegacy() {
  localStorage.setItem(
    'voicestudio.longform.v1',
    JSON.stringify({
      stories: {
        script: 'Preserved text',
        lines: [{ id: 'old', text: 'Preserved line', profileId: null }],
        output: 'older.m4b',
        outputScript: 'Preserved timing',
        outputChapters: [{ title: 'Original', status: 'done' }],
      },
    }),
  );
}
it('keeps legacy hidden without automatic requests and hides it while connection is unknown', async () => {
  seedLegacy();
  const session = await load();
  expect(session.longformSession.state.drafts.stories.output).toBe('');
  expect(session.getLongformResultState('stories').status).toBe('legacy');
  expect(fixture.fetch).not.toHaveBeenCalled();
  publish({ ...fixture.status, stage: 'attaching' });
  expect(session.getLongformResultState('stories').status).toBe('unknown');
  expect(await session.recoverLegacyLongformResult('stories')).toBe(false);
  expect(fixture.fetch).not.toHaveBeenCalled();
});
it('preserves an imported unknown owner rather than stamping the selected backend', async () => {
  const session = await load();
  session.replaceLongformDraft('stories', {
    ...session.blankLongformDraft(),
    script: 'Imported edits',
    output: 'imported.m4b',
    outputOwner: '00000000-0000-4000-8000-000000000000',
  });
  expect(session.longformSession.state.drafts.stories.script).toBe('Imported edits');
  expect(session.longformSession.state.drafts.stories.output).toBe('');
  expect(session.getLongformResultState('stories').status).toBe('legacy');
  expect(fixture.fetch).not.toHaveBeenCalled();
});
it('checks only the saved filename through current proxy and retains legacy evidence', async () => {
  seedLegacy();
  const session = await load();
  fixture.fetch.mockResolvedValue(new Response(null, { status: 200 }));
  expect(await session.recoverLegacyLongformResult('stories')).toBe(true);
  expect(fixture.fetch).toHaveBeenCalledWith(
    '/audio/older.m4b',
    expect.objectContaining({ method: 'HEAD' }),
  );
  expect(session.longformSession.state.drafts.stories.output).toBe('older.m4b');
  session.flushLongformSessionPersistence();
  expect(
    JSON.parse(localStorage.getItem('voicestudio.longform.v1')!).__resultOwnership.stories.legacy
      .output,
  ).toBe('older.m4b');
  expect(session.getLongformResultState('stories').availabilityOnly).toBe(true);
});
it.each(['switch-back', 'replace', 'reset', 'auth'] as const)(
  'rejects a late HEAD reply after %s',
  async (change) => {
    seedLegacy();
    const session = await load();
    let respond!: (response: Response) => void;
    fixture.fetch.mockReturnValue(
      new Promise<Response>((resolve) => {
        respond = resolve;
      }),
    );
    const pending = session.recoverLegacyLongformResult('stories');
    const signal = fixture.fetch.mock.calls[0][1]?.signal as AbortSignal;
    if (change === 'switch-back') {
      publish(local());
      publish(remote());
    } else if (change === 'replace')
      session.replaceLongformDraft('stories', {
        ...session.blankLongformDraft(),
        script: 'Replacement',
      });
    else if (change === 'reset') session.clearLongformDraftForReset();
    else window.dispatchEvent(new CustomEvent('ov:auth-required', { detail: { mode: 'apikey' } }));
    expect(signal?.aborted).toBe(true);
    respond(new Response(null, { status: 200 }));
    expect(await pending).toBe(false);
    expect(session.longformSession.state.drafts.stories.output).toBe('');
  },
);
it('retains a known start-owner completion while another connection is selected', async () => {
  const session = await load();
  let respond!: (response: Response) => void;
  fixture.fetch.mockReturnValue(
    new Promise<Response>((resolve) => {
      respond = resolve;
    }),
  );
  const pending = session.renderLongform('stories');
  publish(local());
  respond(events('late-remote.m4b'));
  await pending;
  expect(session.longformSession.state.drafts.stories.output).toBe('');
  publish(remote());
  expect(session.longformSession.state.drafts.stories.output).toBe('late-remote.m4b');
  expect(session.longformSession.state.drafts.stories.outputChapters[0]?.duration_ms).toBe(100);
});

it('saves opaque provenance with a real project round trip but never the private connection map', async () => {
  const session = await load();
  const { createProjectLibrary } = await import('./project-library');
  let durable: DurableLongformRecord | null = null;
  const library = createProjectLibrary({
    read: async () => durable,
    write: async (value) => {
      durable = structuredClone(value);
    },
    clear: async () => {
      durable = null;
    },
  });
  fixture.fetch.mockResolvedValue(events('remote.m4b'));
  await session.renderLongform('audiobook');
  const token = session.longformSession.state.drafts.audiobook.outputOwner;
  expect(token).toMatch(/^[a-f0-9-]{36}$/);
  await library.save('Original', 'audiobook', session.longformSession.state.drafts.audiobook);
  const project = (await library.list())[0];
  expect(project.draft.outputOwner).toBe(token);
  expect(JSON.stringify(project)).not.toContain('remote.fixture.invalid');
  expect(JSON.stringify(project)).not.toContain('__resultOwnership');
  publish(local());
  session.replaceLongformDraft('audiobook', project.draft);
  expect(session.longformSession.state.drafts.audiobook.output).toBe('');
  publish(remote());
  expect(session.longformSession.state.drafts.audiobook.output).toBe('remote.m4b');
});
it('explicit draft replacement retires every previous backend slot without deleting the replacement', async () => {
  const session = await load();
  fixture.fetch.mockResolvedValue(events('remote.m4b'));
  await session.renderLongform('stories');
  publish(local());
  fixture.fetch.mockResolvedValue(events('local.m4b'));
  await session.renderLongform('stories');
  session.replaceLongformDraft('stories', { ...session.blankLongformDraft(), script: 'New draft' });
  for (const status of [remote(), local()]) {
    publish(status);
    expect(session.longformSession.state.drafts.stories.output).toBe('');
    expect(session.longformSession.state.drafts.stories.script).toBe('New draft');
  }
});
it.each([200, 404])(
  'actual recovery control makes no request before the user click (%s)',
  async (status) => {
    seedLegacy();
    const session = await load();
    await import('@/i18n');
    const { LongformResultRecovery } = await import('./result-recovery');
    const View = () => {
      session.useLongformSession();
      return createElement(LongformResultRecovery, {
        state: session.getLongformResultState('stories'),
        disabled: false,
        onRecover: () => {
          void session.recoverLegacyLongformResult('stories');
        },
      });
    };
    render(createElement(View));
    expect(fixture.fetch).not.toHaveBeenCalled();
    fixture.fetch.mockResolvedValue(new Response(null, { status }));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Check saved audio on this connection' }));
    });
    await waitFor(() => expect(session.getLongformResultState('stories').recovering).toBe(false));
    expect(fixture.fetch).toHaveBeenCalledTimes(1);
    if (status === 200) {
      expect(session.longformSession.state.drafts.stories.output).toBe('older.m4b');
      expect(screen.getByText(/may be a different file/)).toBeInTheDocument();
    } else {
      expect(session.longformSession.state.drafts.stories.output).toBe('');
      expect(screen.getByRole('alert')).toHaveTextContent('not available on this connection');
    }
  },
);

it('retains the availability-only warning and legacy evidence after reload', async () => {
  seedLegacy();
  let session = await load();
  fixture.fetch.mockResolvedValue(new Response(null, { status: 200 }));
  expect(await session.recoverLegacyLongformResult('stories')).toBe(true);
  session.flushLongformSessionPersistence();
  fixture.listeners.clear();
  vi.resetModules();
  session = await load();
  expect(session.longformSession.state.drafts.stories.output).toBe('older.m4b');
  expect(session.getLongformResultState('stories').availabilityOnly).toBe(true);
});

it('does not upgrade checked availability through named-project Save, New and Open', async () => {
  seedLegacy();
  const session = await load();
  const { createProjectLibrary } = await import('./project-library');
  let durable: DurableLongformRecord | null = null;
  const library = createProjectLibrary({
    read: async () => durable,
    write: async (value) => {
      durable = structuredClone(value);
    },
    clear: async () => {
      durable = null;
    },
  });
  fixture.fetch.mockResolvedValue(new Response(null, { status: 200 }));
  expect(await session.recoverLegacyLongformResult('stories')).toBe(true);
  session.flushLongformSessionPersistence();
  const originalLegacy = JSON.parse(localStorage.getItem('voicestudio.longform.v1')!)
    .__resultOwnership.stories.legacy;
  await library.save('Checked old result', 'stories', session.longformSession.state.drafts.stories);
  session.replaceLongformDraft('stories', session.blankLongformDraft());
  const project = (await library.list())[0];
  session.replaceLongformDraft('stories', project.draft);
  expect(session.longformSession.state.drafts.stories.output).toBe('');
  expect(session.getLongformResultState('stories').status).toBe('legacy');
  expect(fixture.fetch).toHaveBeenCalledTimes(1);
  session.flushLongformSessionPersistence();
  const reopenedLegacy = JSON.parse(localStorage.getItem('voicestudio.longform.v1')!)
    .__resultOwnership.stories.legacy;
  expect(reopenedLegacy).toEqual(originalLegacy);
  expect(project.draft.outputOwner).toBeNull();
  expect(await session.recoverLegacyLongformResult('stories')).toBe(true);
  expect(session.getLongformResultState('stories').availabilityOnly).toBe(true);
  expect(fixture.fetch).toHaveBeenCalledTimes(2);
});

it('does not rewrite availability-only results on unchanged-connection heartbeats', async () => {
  seedLegacy();
  const session = await load();
  fixture.fetch.mockResolvedValue(new Response(null, { status: 200 }));
  expect(await session.recoverLegacyLongformResult('stories')).toBe(true);
  session.flushLongformSessionPersistence();
  const previous = session.longformSession.state;
  const writes = vi.spyOn(Storage.prototype, 'setItem');
  publish({ ...fixture.status, elapsedMs: 1000, logTail: ['heartbeat'] });
  session.flushLongformSessionPersistence();
  expect(session.longformSession.state).toBe(previous);
  expect(writes).not.toHaveBeenCalled();
});
