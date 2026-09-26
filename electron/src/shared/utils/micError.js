/**
 * micError — maps getUserMedia() failures to actionable i18n messages (#323).
 *
 * Browsers/WebViews reject getUserMedia with a DOMException whose `name`
 * encodes the real cause. Before this existed, every failure showed the same
 * "Microphone access denied" toast — including cases where access was fine
 * but no device was present, or another app held the mic. The permission
 * case additionally gets a per-OS hint telling the user exactly where to
 * re-enable mic access for the app.
 *
 * Pure module (no React, no i18next import) so it is unit-testable and
 * usable from both components and hooks.
 */

/** Detect the user's OS family for the "where to fix it" hint. */
export function detectPlatform(nav = typeof navigator !== 'undefined' ? navigator : undefined) {
  const p = nav?.userAgentData?.platform || nav?.platform || '';
  if (/mac|iphone|ipad/i.test(p)) return 'mac';
  if (/win/i.test(p)) return 'windows';
  return 'linux';
}

/** i18n key for the per-platform "enable the mic in OS settings" hint. */
export function micHintKey(platform) {
  if (platform === 'mac') return 'capture.mic_hint_mac';
  if (platform === 'windows') return 'capture.mic_hint_windows';
  return 'capture.mic_hint_linux';
}

/**
 * Map a getUserMedia error to `{ key, params?, hintKey? }`.
 *
 * `key` is an i18n key; when `hintKey` is set, its translation must be
 * interpolated as the `hint` param of `key` (mic_denied_toast does this).
 */
export function describeMicError(err, platform = detectPlatform()) {
  switch (err?.name) {
    // Permission denied — by the OS privacy setting, the WebView engine,
    // or a non-secure context. Actionable: per-OS settings path.
    case 'NotAllowedError':
    case 'PermissionDeniedError':
    case 'SecurityError':
      return { key: 'capture.mic_denied_toast', hintKey: micHintKey(platform) };

    // No usable input device.
    case 'NotFoundError':
    case 'DevicesNotFoundError':
    case 'OverconstrainedError':
      return { key: 'capture.mic_not_found' };

    // Device exists but cannot be started (held by another app, driver issue).
    case 'NotReadableError':
    case 'TrackStartError':
    case 'AbortError':
      return { key: 'capture.mic_in_use' };

    default:
      return {
        key: 'capture.mic_error_generic',
        params: { message: err?.message || String(err ?? 'unknown') },
      };
  }
}

/** Convenience: resolve the description to a final translated string. */
export function micErrorMessage(t, err, platform = detectPlatform()) {
  const d = describeMicError(err, platform);
  const params = { ...d.params };
  if (d.hintKey) params.hint = t(d.hintKey);
  return t(d.key, params);
}
