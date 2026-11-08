import { expect, it } from 'vitest';
import { LoopbackError, startLoopback } from './loopback';

const page = 'https://app.voicestudio.sh/app/desktop-signed-in';
const hit = (uri: string, query: string) =>
  fetch(uri + query, { redirect: 'manual' }).then((r) => ({
    status: r.status,
    location: r.headers.get('location'),
  }));

it('binds only 127.0.0.1 on a random port', async () => {
  const lb = await startLoopback({ expectedState: 's', signedInPage: page });
  expect(lb.redirectUri).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/callback$/);
  lb.close();
  await expect(lb.code).rejects.toBeInstanceOf(LoopbackError);
});

it('refuses a wrong state and keeps waiting for the right one', async () => {
  const lb = await startLoopback({ expectedState: 'good', signedInPage: page });
  expect((await hit(lb.redirectUri, '?code=x&state=bad')).status).toBe(400);
  const ok = await hit(lb.redirectUri, '?code=real&state=good');
  expect(ok).toEqual({ status: 302, location: page });
  await expect(lb.code).resolves.toBe('real');
});

it('answers 404 off the callback path', async () => {
  const lb = await startLoopback({ expectedState: 's', signedInPage: page });
  expect((await hit(lb.redirectUri.replace('/callback', '/other'), '?state=s&code=c')).status).toBe(
    404,
  );
  lb.close();
  await lb.code.catch(() => {});
});

it('reports a denied sign-in', async () => {
  const lb = await startLoopback({ expectedState: 's', signedInPage: page });
  expect(await hit(lb.redirectUri, '?error=access_denied&state=s')).toEqual({
    status: 302,
    location: `${page}?error=access_denied`,
  });
  await expect(lb.code).rejects.toMatchObject({ reason: 'denied' });
});

it('times out', async () => {
  const lb = await startLoopback({
    expectedState: 's',
    signedInPage: page,
    timeoutMs: 50,
  });
  await expect(lb.code).rejects.toMatchObject({ reason: 'timeout' });
});

it('refuses connections once closed', async () => {
  const lb = await startLoopback({ expectedState: 's', signedInPage: page });
  lb.close();
  await lb.code.catch(() => {});
  await expect(hit(lb.redirectUri, '?code=c&state=s')).rejects.toBeTruthy();
});

it('a state with the same length but different bytes is refused, not a crash', async () => {
  const lb = await startLoopback({
    expectedState: 'a'.repeat(43),
    signedInPage: page,
  });
  const multibyte = encodeURIComponent('é'.repeat(43));
  expect((await hit(lb.redirectUri, `?code=x&state=${multibyte}`)).status).toBe(400);
  expect((await hit(lb.redirectUri, `?code=real&state=${'a'.repeat(43)}`)).status).toBe(302);
  await expect(lb.code).resolves.toBe('real');
});
