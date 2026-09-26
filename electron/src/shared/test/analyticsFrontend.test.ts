import { describe, it, expect, vi } from 'vitest';
import {
  disableAnalytics,
  sanitizeException,
  sanitizeProps,
  capturePageview,
  sanitizeOutgoingEvent,
  hardenedConfig,
  initAnalyticsFromConsent,
} from '../utils/analytics';

// The usual posthog-js integration is `posthog.init(...)` at module load with
// autocapture on. Both halves would be a disaster here:
//
//   - init-at-load tracks every user BEFORE they consent, which makes the app's
//     own promise ("sends nothing out of the box") false;
//   - autocapture sends the TEXT CONTENT of DOM elements — in this app, the
//     script the user is about to synthesise, their voice names, their filenames.
//
// These tests pin both, plus the allowlist that stops a future caller leaking
// content by adding a field.

describe('hardenedConfig — the settings that make the promises true', () => {
  it('disables autocapture, session recording and pageview capture', () => {
    const c = hardenedConfig();
    expect(c.autocapture).toBe(false); // would send DOM text
    expect(c.disable_session_recording).toBe(true); // would record the screen
    expect(c.capture_exceptions).toBe(false); // raw exception messages and paths
    expect(c.capture_pageview).toBe(false);
    expect(c.capture_pageleave).toBe(false);
  });

  it('starts opted OUT, so init alone never captures anything', () => {
    expect(hardenedConfig().opt_out_capturing_by_default).toBe(true);
  });

  it('masks text and attributes as defence in depth', () => {
    const c = hardenedConfig();
    expect(c.mask_all_text).toBe(true);
    expect(c.mask_all_element_attributes).toBe(true);
  });
});

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

describe('sanitizeException — error tracking keeps frames, not user data', () => {
  it('replaces the message and removes credentials, home paths and extension frames', () => {
    const failure = new TypeError(
      'Private script at /home/pal/secret with token sk-12345678901234567890',
    );
    failure.stack = [
      failure.message,
      '    at render (/home/pal/github/VoiceStudio/electron/src/app.tsx:12:3)',
      '    at extension (chrome-extension://private/content.js:2:1)',
    ].join('\n');
    const clean = sanitizeException(failure);
    expect(clean.name).toBe('TypeError');
    expect(clean.message).toBe('VoiceStudio renderer error');
    expect(clean.stack).toContain('~/github/VoiceStudio/electron/src/app.tsx:12:3');
    expect(clean.stack).not.toContain('Private script');
    expect(clean.stack).not.toContain('/home/pal');
    expect(clean.stack).not.toContain('sk-');
    expect(clean.stack).not.toContain('chrome-extension');
  });
});

describe('capturePageview — Web Analytics receives fixed app paths only', () => {
  it('is safe before analytics consent resolves and rejects dynamic screen labels', () => {
    expect(() => capturePageview('view:clone')).not.toThrow();
    expect(() => capturePageview('view:integrations/private-project')).not.toThrow();
  });
});

describe('sanitizeOutgoingEvent — renderer URLs never leave', () => {
  it('removes SDK-added URL metadata from product events', () => {
    const clean = sanitizeOutgoingEvent({
      event: 'screen_viewed',
      properties: {
        stage: 'view:integrations',
        $current_url: 'http://localhost:3902/#/integrations/private-slug',
        $pathname: '/',
        $title: 'Private project',
        $referrer: 'https://private.example/',
      },
    });
    expect(clean?.properties).toEqual({ stage: 'view:integrations' });
  });

  it('keeps only the synthetic app URL for manual pageviews', () => {
    const clean = sanitizeOutgoingEvent({
      event: '$pageview',
      properties: {
        $current_url: 'http://localhost:3902/#/clone/private-id',
        $pathname: '/clone',
        $title: 'Private voice',
      },
    });
    expect(clean?.properties).toEqual({
      $current_url: 'https://app.voicestudio.sh/clone',
      $host: 'app.voicestudio.sh',
      $pathname: '/clone',
    });
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
    vi.mock('posthog-js/dist/module.slim.no-external', () => ({
      default: {
        init: vi.fn(),
        opt_in_capturing: vi.fn(),
        opt_out_capturing: vi.fn(),
        reset: vi.fn(),
        has_opted_out_capturing: () => false,
        capture: vi.fn(),
      },
    }));
    const started = await initAnalyticsFromConsent(async () => ({
      available: true,
      opted_in: true,
    }));
    expect(started).toBe(true);
  });
});

it('drops multiline error messages while preserving genuine stack frames', () => {
  const error = new Error('Failed\nPrivate transcript\nCustomer address');
  error.stack =
    'Error: Failed\nPrivate transcript\nCustomer address\n    at render (app://voicestudio/assets/index.js:3:4)';
  const safe = sanitizeException(error);
  expect(safe.stack).not.toContain('Private transcript');
  expect(safe.stack).not.toContain('Customer address');
  expect(safe.stack).toContain('index.js:3:4');
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
