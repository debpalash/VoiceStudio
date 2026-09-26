import { describe, it, expect } from 'vitest';
import { describeMicError, detectPlatform, micErrorMessage, micHintKey } from './micError';

const err = (name, message = '') => {
  const e = new Error(message);
  e.name = name;
  return e;
};

describe('describeMicError', () => {
  it.each(['NotAllowedError', 'PermissionDeniedError', 'SecurityError'])(
    'maps %s to the denied toast with a platform hint',
    (name) => {
      const d = describeMicError(err(name), 'windows');
      expect(d.key).toBe('capture.mic_denied_toast');
      expect(d.hintKey).toBe('capture.mic_hint_windows');
    },
  );

  it('picks the per-platform hint key', () => {
    expect(describeMicError(err('NotAllowedError'), 'mac').hintKey).toBe('capture.mic_hint_mac');
    expect(describeMicError(err('NotAllowedError'), 'windows').hintKey).toBe(
      'capture.mic_hint_windows',
    );
    expect(describeMicError(err('NotAllowedError'), 'linux').hintKey).toBe(
      'capture.mic_hint_linux',
    );
  });

  it.each(['NotFoundError', 'DevicesNotFoundError', 'OverconstrainedError'])(
    'maps %s to mic_not_found',
    (name) => {
      expect(describeMicError(err(name), 'windows')).toEqual({ key: 'capture.mic_not_found' });
    },
  );

  it.each(['NotReadableError', 'TrackStartError', 'AbortError'])(
    'maps %s to mic_in_use',
    (name) => {
      expect(describeMicError(err(name), 'linux')).toEqual({ key: 'capture.mic_in_use' });
    },
  );

  it('falls back to a generic message carrying the error text', () => {
    const d = describeMicError(err('SomethingElse', 'boom'), 'mac');
    expect(d.key).toBe('capture.mic_error_generic');
    expect(d.params).toEqual({ message: 'boom' });
  });

  it('survives undefined errors', () => {
    const d = describeMicError(undefined, 'mac');
    expect(d.key).toBe('capture.mic_error_generic');
    expect(d.params.message).toBeTruthy();
  });
});

describe('micErrorMessage', () => {
  // Fake t: returns "key|param=value" so interpolation is observable.
  const t = (key, params = {}) =>
    [key, ...Object.entries(params).map(([k, v]) => `${k}=${v}`)].join('|');

  it('interpolates the translated hint into the denied toast', () => {
    expect(micErrorMessage(t, err('NotAllowedError'), 'windows')).toBe(
      'capture.mic_denied_toast|hint=capture.mic_hint_windows',
    );
  });

  it('passes the raw message through for unknown errors', () => {
    expect(micErrorMessage(t, err('WeirdError', 'no device bus'), 'linux')).toBe(
      'capture.mic_error_generic|message=no device bus',
    );
  });

  it('translates device errors without params', () => {
    expect(micErrorMessage(t, err('NotFoundError'), 'mac')).toBe('capture.mic_not_found');
  });
});

describe('detectPlatform / micHintKey', () => {
  it('detects from navigator.userAgentData.platform first', () => {
    expect(detectPlatform({ userAgentData: { platform: 'Windows' }, platform: 'MacIntel' })).toBe(
      'windows',
    );
  });

  it('falls back to navigator.platform', () => {
    expect(detectPlatform({ platform: 'MacIntel' })).toBe('mac');
    expect(detectPlatform({ platform: 'Win32' })).toBe('windows');
    expect(detectPlatform({ platform: 'Linux x86_64' })).toBe('linux');
  });

  it('defaults to linux when nothing is known', () => {
    expect(detectPlatform({})).toBe('linux');
    expect(detectPlatform(undefined)).toBe('linux');
  });

  it('maps platforms to hint keys', () => {
    expect(micHintKey('mac')).toBe('capture.mic_hint_mac');
    expect(micHintKey('windows')).toBe('capture.mic_hint_windows');
    expect(micHintKey('linux')).toBe('capture.mic_hint_linux');
  });
});
