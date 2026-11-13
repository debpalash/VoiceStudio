import { describe, it, expect, vi } from 'vitest';
import {
  capture,
  capturePageview,
  captureException,
  disableAnalytics,
  enableAnalytics,
  initAnalyticsFromConsent,
  sanitizeProps,
  setAnalyticsSender,
} from '../utils/analytics';

// The interface never calls the analytics service itself: after consent it hands
// allowlisted events to the local backend. These tests pin that nothing moves
// before consent, that content cannot ride along, and that a withdrawal stops it.

describe('sanitizeProps — content cannot get out, even by accident', () => {
  it('drops anything not on the allowlist', () => {
    const clean = sanitizeProps({
      text: 'my private script about a confidential merger',
      audio_path: '/Users/someone/voice.wav',
      voice_name: "Grandma's voice",
      email: 'a@b.com',
      engine_id: 'omnivoice',
      language: 'en',
      text_length: 120,
      has_profile: true,
    });
    expect(clean).toEqual({
      engine_id: 'omnivoice',
      language: 'en',
      text_length: 120,
      has_profile: true,
    });
    const blob = JSON.stringify(clean);
    expect(blob).not.toContain('confidential');
    expect(blob).not.toContain('/Users/');
    expect(blob).not.toContain('Grandma');
  });

  it('refuses a long string even on an allowlisted key', () => {
    expect(sanitizeProps({ language: 'x'.repeat(500), engine_id: 'omnivoice' })).toEqual({
      engine_id: 'omnivoice',
    });
  });

  it('is safe on empty input', () => {
    expect(sanitizeProps()).toEqual({});
    expect(sanitizeProps({})).toEqual({});
  });
});

describe('initAnalyticsFromConsent — silence is not consent', () => {
  it('does NOT start analytics when the user has not opted in', async () => {
    const started = await initAnalyticsFromConsent(async () => ({
      available: true,
      opted_in: false,
    }));
    expect(started).toBe(false);
  });

  it('does NOT start analytics when the build ships no destination', async () => {
    const started = await initAnalyticsFromConsent(async () => ({
      available: false,
      opted_in: true,
    }));
    expect(started).toBe(false);
  });

  it('does NOT start analytics when the backend is unreachable', async () => {
    const started = await initAnalyticsFromConsent(async () => {
      throw new Error('backend down');
    });
    expect(started).toBe(false); // fails CLOSED
  });

  it('starts ONLY when the user opted in and a destination exists', async () => {
    const started = await initAnalyticsFromConsent(async () => ({
      available: true,
      opted_in: true,
    }));
    expect(started).toBe(true);
  });
});


it('sends nothing until the user has opted in, then stops on withdrawal', async () => {
  const sent = vi.fn(async () => ({}));
  setAnalyticsSender(sent);
  disableAnalytics();
  capture('feature_used', { feature: 'clone' });
  capturePageview('view:home');
  expect(sent).not.toHaveBeenCalled();

  await enableAnalytics();
  capture('feature_used', { feature: 'clone', text: 'private script' });
  capturePageview('view:home');
  expect(sent).toHaveBeenCalledWith({ event: 'feature_used', props: { feature: 'clone' } });
  expect(sent).toHaveBeenCalledWith({ event: 'pageview', props: {}, screen: 'view:home' });

  sent.mockClear();
  disableAnalytics();
  capture('feature_used', { feature: 'clone' });
  expect(sent).not.toHaveBeenCalled();
});

it('reduces a renderer failure to a coarse class and stage', async () => {
  const sent = vi.fn(async () => ({}));
  setAnalyticsSender(sent);
  disableAnalytics();
  await enableAnalytics();
  const error = new Error('Private transcript in /Users/me/secret.wav');
  error.name = 'CustomerAddressError';
  captureException(error, 'renderer:uncaught');
  expect(sent).toHaveBeenCalledWith({
    event: 'renderer_error',
    props: { error_class: 'Error', stage: 'renderer:uncaught' },
  });
  expect(JSON.stringify(sent.mock.calls)).not.toContain('Private');
  disableAnalytics();
});

it('accepts only engine keys as engine_id', () => {
  expect(sanitizeProps({ engine_id: 'My Cool Voice' })).toEqual({});
  expect(sanitizeProps({ engine_id: '/home/me/model.bin' })).toEqual({});
  expect(sanitizeProps({ engine_id: 'cosyvoice-3' })).toEqual({ engine_id: 'cosyvoice-3' });
});

it('does not revive analytics when a stale consent request resolves after opt-out', async () => {
  let finish!: (value: { available: boolean; opted_in: boolean }) => void;
  const pending = initAnalyticsFromConsent(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  disableAnalytics();
  finish({ available: true, opted_in: true });
  expect(await pending).toBe(false);
});
