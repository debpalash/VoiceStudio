import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './client';
import {
  callEventsUrl,
  callRecordingUrl,
  deleteCall,
  renderDisclosure,
  callTime,
  callsUnavailable,
  getReadiness,
  hangUp,
  listCalls,
  parseCallEvent,
  saveCallSettings,
  sayOnCall,
  setTakeover,
  startCall,
} from './calls';

const fetchMock = vi.fn();
function respond(body: unknown, status = 200) {
  fetchMock.mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}
function lastRequest() {
  const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit | undefined];
  return {
    url,
    method: init?.method ?? 'GET',
    body: init?.body ? JSON.parse(String(init.body)) : undefined,
    type: new Headers(init?.headers).get('Content-Type'),
  };
}

vi.stubGlobal('fetch', fetchMock);
afterEach(() => fetchMock.mockReset());

describe('calls client', () => {
  it('starts a call with a JSON body and unwraps the record', async () => {
    const call = { id: 'c1', status: 'dialing' };
    respond({ call }, 201);
    await expect(
      startCall({ to: '+15550100199', brief: 'Book a table', profile_id: 'p1', disclosure: 'Hi' }),
    ).resolves.toEqual(call);
    expect(lastRequest()).toEqual({
      url: '/api/calls',
      method: 'POST',
      body: { to: '+15550100199', brief: 'Book a table', profile_id: 'p1', disclosure: 'Hi' },
      type: 'application/json',
    });
  });

  it('lists calls with a limit and hits every control endpoint', async () => {
    respond({ calls: [{ id: 'a' }] });
    await expect(listCalls(20)).resolves.toEqual([{ id: 'a' }]);
    expect(lastRequest().url).toBe('/api/calls?limit=20');

    respond({ ok: true });
    await sayOnCall('a/b', 'Hello');
    expect(lastRequest()).toMatchObject({ url: '/api/calls/a%2Fb/say', body: { text: 'Hello' } });
    respond({ ok: true });
    await setTakeover('a', true);
    expect(lastRequest()).toMatchObject({ url: '/api/calls/a/takeover', body: { enabled: true } });
    respond({ ok: true });
    await hangUp('a');
    expect(lastRequest()).toMatchObject({ url: '/api/calls/a/hangup', method: 'POST' });
    respond({ inbound_mode: 'agent' });
    await saveCallSettings({ inbound_mode: 'agent' });
    expect(lastRequest()).toMatchObject({ url: '/api/calls/settings', method: 'PUT' });
    expect(callEventsUrl('a')).toBe('/api/calls/a/events');
  });

  it('accepts readiness as a bare list or wrapped in items', async () => {
    const items = [{ id: 'credentials', ok: false, detail: 'Add your SID' }];
    respond(items);
    await expect(getReadiness()).resolves.toEqual(items);
    respond({ items });
    await expect(getReadiness()).resolves.toEqual(items);
  });

  it('treats a missing route as "backend predates Calls" and nothing else', async () => {
    respond({ detail: 'Not Found' }, 404);
    const error = await getReadiness().catch((reason: unknown) => reason);
    expect(callsUnavailable(error)).toBe(true);
    expect(callsUnavailable(new ApiError(500, 'boom'))).toBe(false);
    expect(callsUnavailable(new ApiError(0, 'offline'))).toBe(false);
    expect(callsUnavailable(new Error('x'))).toBe(false);
  });
});

describe('parseCallEvent', () => {
  it('parses each event type, from the data type or the SSE event name', () => {
    expect(parseCallEvent('{"type":"status","status":"ringing"}')).toMatchObject({
      type: 'status',
      status: 'ringing',
    });
    expect(
      parseCallEvent('{"speaker":"caller","text":"Hi","final":false,"t":1.5}', 'transcript'),
    ).toEqual({
      type: 'transcript',
      speaker: 'caller',
      text: 'Hi',
      final: false,
      t: 1.5,
    });
    expect(parseCallEvent('{"type":"agent_state","state":"thinking"}')).toEqual({
      type: 'agent_state',
      state: 'thinking',
    });
    expect(parseCallEvent('{"type":"outcome","outcome":"booked","summary":"8pm"}')).toEqual({
      type: 'outcome',
      outcome: 'booked',
      summary: '8pm',
    });
    expect(parseCallEvent('{"type":"ended","duration_s":42}')).toMatchObject({
      type: 'ended',
      duration_s: 42,
    });
  });

  it('drops malformed or unknown payloads', () => {
    expect(parseCallEvent('not json')).toBeNull();
    expect(parseCallEvent('{"type":"mystery"}')).toBeNull();
    expect(parseCallEvent('{"type":"agent_state","state":"sleeping"}')).toBeNull();
    expect(parseCallEvent('{"type":"transcript","speaker":"robot","text":"x"}')).toBeNull();
    expect(parseCallEvent('[1,2]')).toBeNull();
  });
});

it('reads epoch seconds, epoch milliseconds and ISO timestamps', () => {
  expect(callTime(1_700_000_000)).toBe(1_700_000_000_000);
  expect(callTime('1700000000')).toBe(1_700_000_000_000);
  expect(callTime(1_700_000_000_000)).toBe(1_700_000_000_000);
  expect(callTime('2026-09-23T10:00:00Z')).toBe(Date.parse('2026-09-23T10:00:00Z'));
  expect(callTime(null)).toBeNull();
  expect(callTime('nope')).toBeNull();
});

it('deletes calls, links recordings and fills the disclosure name like the backend', async () => {
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
  await deleteCall('c1');
  expect(lastRequest()).toMatchObject({ url: '/api/calls/c1', method: 'DELETE' });
  expect(callRecordingUrl('c1')).toBe('/api/calls/c1/recording');
  expect(renderDisclosure("Hi, this is {name}'s assistant.", ' Sam ')).toBe(
    "Hi, this is Sam's assistant.",
  );
  expect(renderDisclosure('{name} calling', '')).toBe('someone calling');
});
