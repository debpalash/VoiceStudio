/// <reference types="node" />
import { createServer, type ServerResponse } from 'node:http';
import { once } from 'node:events';
import { afterAll, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/react';

const fixture = vi.hoisted(() => ({
  base: '',
  enabled: false,
  epoch: 'a'.repeat(32),
  grants: 0,
  completedEventReads: 0,
  capture: vi.fn(async (_event: unknown) => {}),
}));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({
    diagnostics: {
      getStatus: async () => ({
        enabled: fixture.enabled,
        opted_in: fixture.enabled,
        available: true,
        prompted: true,
        grant_epoch: fixture.enabled ? fixture.epoch : null,
      }),
      setConsent: async (enabled: boolean) => {
        fixture.enabled = enabled;
        if (enabled) fixture.epoch = (++fixture.grants).toString(16).padStart(32, '0');
        return {
          enabled,
          opted_in: enabled,
          available: true,
          prompted: true,
          grant_epoch: enabled ? fixture.epoch : null,
        };
      },
      capture: async (input: unknown, expectedEpoch: string) => {
        if (fixture.enabled && expectedEpoch === fixture.epoch) await fixture.capture(input);
      },
    },
  }),
}));
vi.mock('@/lib/api/client', async (load) => ({
  ...(await load<typeof import('@/lib/api/client')>()),
  apiFetch: (path: string, init: RequestInit) => fetch(fixture.base + path, init),
  apiJson: async (path: string, init?: RequestInit) => {
    const response = await fetch(fixture.base + path, init);
    if (!response.ok) throw new Error('Fixture HTTP failure');
    const body = await response.json();
    if (path.includes('/events?')) fixture.completedEventReads++;
    return body;
  },
}));
import { dubSession, generateDub, resumeDub } from './dub-session';
import { refreshDiagnosticState, setDiagnosticConsent } from '@/lib/diagnostic-reports';

const failure = { type: 'error', reason: 'private archived story content', stage: 'generation' };
const payload = (event: object) => 'data: ' + JSON.stringify(event) + '\n\n';
let mode: 'saved' | 'live' = 'saved';
let eventReads = 0;
let holdVerification = false;
let releaseVerification: (() => void) | undefined;
let baselineRead: Promise<void>;
let markBaselineRead: () => void;
let initialStatus = 'failed';
let holdNewGeneration = false;
let releaseNewGeneration: (() => void) | undefined;
const timestamp = 1_000_000; // Deliberately differs from the renderer clock.
const server = createServer(async (request, response) => {
  const path = new URL(request.url!, 'http://localhost').pathname;
  const send = (body: unknown) => {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify(body));
  };
  const stream = (target: ServerResponse) => {
    target.setHeader('Content-Type', 'text/event-stream');
    target.end(payload(failure));
  };
  if (path === '/dub/generate/media') {
    if (holdNewGeneration)
      await new Promise<void>((resolve) => {
        releaseNewGeneration = resolve;
      });
    return send({ task_id: 'new-task' });
  }
  if (path === '/jobs/task') return send({ id: 'task', status: initialStatus });
  if (path === '/jobs/task/events') {
    eventReads++;
    const baseline = eventReads === 1;
    if (!baseline && holdVerification)
      await new Promise<void>((resolve) => {
        releaseVerification = resolve;
      });
    const events =
      mode === 'saved' || !baseline
        ? [
            {
              seq: 2,
              created_at: timestamp + (mode === 'live' ? 1 : -1),
              payload: payload(failure),
            },
          ]
        : [{ seq: 1, created_at: timestamp, payload: payload({ type: 'progress' }) }];
    send({
      job: {
        id: 'task',
        status: mode === 'saved' ? 'failed' : 'running',
        created_at: timestamp - 2,
        updated_at: timestamp,
      },
      events,
    });
    if (baseline) markBaselineRead();
    return;
  }
  if (path === '/tasks/stream/task') {
    if (mode === 'live') await baselineRead;
    return stream(response);
  }
  if (path === '/tasks/stream/new-task') {
    return stream(response);
  }
  if (path === '/dub/transcribe-stream/media') return stream(response);
  response.statusCode = 404;
  response.end();
});
beforeAll(async () => {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('No local fixture address');
  fixture.base = 'http://127.0.0.1:' + address.port;
});
afterAll(async () => {
  releaseVerification?.();
  releaseNewGeneration?.();
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});
beforeEach(async () => {
  releaseVerification?.();
  releaseNewGeneration?.();
  releaseNewGeneration = undefined;
  holdNewGeneration = false;
  releaseVerification = undefined;
  fixture.enabled = false;
  fixture.capture.mockClear();
  fixture.completedEventReads = 0;
  mode = 'saved';
  eventReads = 0;
  holdVerification = false;
  initialStatus = 'failed';
  baselineRead = new Promise<void>((resolve) => {
    markBaselineRead = resolve;
  });
  await setDiagnosticConsent(false);
  dubSession.setState((current) => ({
    ...current,
    jobId: 'media',
    taskId: 'task',
    recovery: 'generating',
    phase: 'editing',
    quality: 'fast',
    voiceMatch: 'per_line',
    segments: [
      {
        id: '1',
        start: 0,
        end: 2,
        text: 'Synthetic fixture sentence.',
        text_original: 'Synthetic fixture sentence.',
      },
    ],
  }));
});

it.each(['generating', 'preparing'] as const)(
  'displays a saved %s error after OFF→ON without reporting it as new',
  async (recovery) => {
    // The server's saved failure existed while reporting was disabled.
    await refreshDiagnosticState();
    await setDiagnosticConsent(true);
    dubSession.setState((current) => ({ ...current, recovery }));
    await resumeDub();
    expect(dubSession.state.error).toContain('private archived story content');
    expect(dubSession.state.recovery).toBeNull();
    expect(fixture.capture).not.toHaveBeenCalled();
  },
);

it('captures a genuinely new live resumed failure using server sequence evidence despite clock skew', async () => {
  mode = 'live';
  initialStatus = 'running';
  await setDiagnosticConsent(true);
  await resumeDub();
  await waitFor(() => expect(fixture.capture).toHaveBeenCalledOnce());
  expect(eventReads).toBe(2);
  expect(fixture.capture.mock.calls[0][0]).toMatchObject({
    category: 'generation_failed',
    stage: 'dub',
  });
  expect(JSON.stringify(fixture.capture.mock.calls)).not.toContain(
    'private archived story content',
  );
});

it('cannot move a live failure into a later consent epoch while checking its persisted evidence', async () => {
  mode = 'live';
  initialStatus = 'running';
  holdVerification = true;
  await setDiagnosticConsent(true);
  await resumeDub();
  await waitFor(() => expect(releaseVerification).toBeTypeOf('function'));
  await setDiagnosticConsent(false);
  await setDiagnosticConsent(true);
  releaseVerification!();
  await waitFor(() => expect(fixture.completedEventReads).toBe(2));
  expect(fixture.capture).not.toHaveBeenCalled();
});

it('cannot borrow another window owner grant while the local renderer revision stays unchanged', async () => {
  mode = 'live';
  initialStatus = 'running';
  holdVerification = true;
  await setDiagnosticConsent(true);
  await resumeDub();
  await waitFor(() => expect(releaseVerification).toBeTypeOf('function'));
  // The owner changes permission outside this renderer; its adapter sees no update.
  fixture.epoch = 'f'.repeat(32);
  releaseVerification!();
  await waitFor(() => expect(fixture.completedEventReads).toBe(2));
  expect(fixture.capture).not.toHaveBeenCalled();
});

it('keeps fresh request-scoped ASR failures reportable when transcription is resumed', async () => {
  await setDiagnosticConsent(true);
  dubSession.setState((current) => ({ ...current, recovery: 'transcribing', taskId: null }));
  await resumeDub();
  expect(fixture.capture).toHaveBeenCalledOnce();
  expect(fixture.capture.mock.calls[0][0]).toMatchObject({
    category: 'operation_failed',
    stage: 'transcription',
  });
});

it('does not rebind a submitted task to refreshed external owner consent', async () => {
  await setDiagnosticConsent(true);
  holdNewGeneration = true;
  dubSession.setState((current) => ({ ...current, recovery: null, taskId: null }));
  const generation = generateDub('English', 'en');
  try {
    await waitFor(() => expect(releaseNewGeneration).toBeTypeOf('function'));
    fixture.epoch = 'e'.repeat(32);
    await refreshDiagnosticState();
    releaseNewGeneration!();
    await generation;
    expect(fixture.capture).not.toHaveBeenCalled();
  } finally {
    releaseNewGeneration?.();
    await generation;
  }
});

it('keeps a newly submitted generation worker failure reportable', async () => {
  await setDiagnosticConsent(true);
  dubSession.setState((current) => ({ ...current, recovery: null, taskId: null }));
  await generateDub('English', 'en');
  await waitFor(() => expect(fixture.capture).toHaveBeenCalledOnce());
  expect(fixture.capture.mock.calls[0][0]).toMatchObject({
    category: 'generation_failed',
    stage: 'dub',
  });
});
