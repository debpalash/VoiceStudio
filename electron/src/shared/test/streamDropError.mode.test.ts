import { describe, it, expect } from 'vitest';
import { streamDropError } from '../utils/backendCrash';

// A dropped stream with a healthy backend gets different advice depending on
// where the UI runs: a reverse-proxy hint only makes sense for a served page.

const noCrash = async () => null;
const alive = async () => true;
const fast = { waitMs: 0, intervalMs: 1, sleep: async () => {}, probeAlive: alive };

describe('streamDropError, backend still answering', () => {
  it('does not mention proxies in desktop mode', async () => {
    const err = await streamDropError('fallback', noCrash, { ...fast, mode: 'desktop' });
    expect(err.message).toMatch(/still running/);
    expect(err.message).not.toMatch(/proxy|nginx|buffering/i);
    expect(err.message).toMatch(/Logs/);
  });

  it('does not mention proxies in dev mode', async () => {
    const err = await streamDropError('fallback', noCrash, { ...fast, mode: 'dev' });
    expect(err.message).not.toMatch(/proxy|nginx|buffering/i);
  });

  it('keeps the reverse-proxy advice in server mode', async () => {
    const err = await streamDropError('fallback', noCrash, { ...fast, mode: 'server' });
    expect(err.message).toMatch(/proxy_buffering off/);
  });

  it('still falls back to the caller message when the backend is gone', async () => {
    const err = await streamDropError('fallback', noCrash, {
      ...fast,
      probeAlive: async () => false,
      mode: 'desktop',
    });
    expect(err.message).toBe('fallback');
  });
});
