import { beforeEach, describe, expect, it } from 'vitest';
import {
  readSetupAnalytics,
  rememberSetupAnalytics,
  setupAnalyticsAllowed,
  SetupFunnel,
  SETUP_ANALYTICS_KEY,
} from './setup-analytics';

beforeEach(() => localStorage.clear());

describe('early consent', () => {
  it('is off unless the user said yes and the backend has not taken over', () => {
    expect(setupAnalyticsAllowed(null, false)).toBe(false);
    expect(setupAnalyticsAllowed({ enabled: false, settled: false }, false)).toBe(false);
    expect(setupAnalyticsAllowed({ enabled: true, settled: false }, false)).toBe(true);
    // Otherwise a later Settings "off" could be undone when the backend goes down.
    expect(setupAnalyticsAllowed({ enabled: true, settled: true }, false)).toBe(false);
    expect(setupAnalyticsAllowed({ enabled: true, settled: false }, true)).toBe(false);
  });

  it('reads damaged storage as not asked', () => {
    localStorage.setItem(SETUP_ANALYTICS_KEY, '{oops');
    expect(readSetupAnalytics()).toBeNull();
    localStorage.setItem(SETUP_ANALYTICS_KEY, '{"enabled":"yes"}');
    expect(readSetupAnalytics()).toBeNull();
  });

  it('round-trips a choice', () => {
    rememberSetupAnalytics({ enabled: true, settled: false });
    expect(readSetupAnalytics()).toEqual({ enabled: true, settled: false });
  });
});

describe('SetupFunnel', () => {
  const names = (events: { event: string }[]) => events.map(({ event }) => event);

  it('follows one successful first run', () => {
    const funnel = new SetupFunnel();
    expect(funnel.observe({ stage: 'setup_required' }, false)).toEqual([]);
    expect(names(funnel.observe({ stage: 'installing', setupPhase: 'checking' }, false))).toEqual([
      'setup_started',
    ]);
    expect(funnel.observe({ stage: 'installing', setupPhase: 'installing_deps' }, false)).toEqual([]);
    expect(names(funnel.observe({ stage: 'starting' }, false))).toEqual(['setup_completed']);
    expect(names(funnel.observe({ stage: 'ready' }, false))).toEqual(['backend_first_ready']);
    expect(funnel.observe({ stage: 'ready' }, false)).toEqual([]);
    expect(names(new SetupFunnel().observe({ stage: 'ready' }, false))).toEqual([]);
  });

  it('counts a failure once per attempt and code, and sends only stage and code', () => {
    const funnel = new SetupFunnel();
    funnel.observe({ stage: 'installing' }, false);
    const failed = { stage: 'setup_required', failure: { code: 'EXEC_REFUSED' } };
    const [event] = funnel.observe(failed, false);
    expect(event).toEqual({
      event: 'setup_failed',
      props: { stage: 'setup_required', error_class: 'EXEC_REFUSED' },
    });
    expect(funnel.observe(failed, false)).toEqual([]);
    funnel.observe({ stage: 'installing' }, false);
    expect(names(funnel.observe(failed, false))).toEqual(['setup_failed']);
  });

  it('stays quiet once setup has been completed', () => {
    const funnel = new SetupFunnel();
    expect(funnel.observe({ stage: 'installing' }, true)).toEqual([]);
    expect(funnel.observe({ stage: 'crashed', failure: { code: 'NATIVE_CRASH' } }, true)).toEqual([]);
    expect(funnel.observe({ stage: 'ready' }, true)).toEqual([]);
  });
});
