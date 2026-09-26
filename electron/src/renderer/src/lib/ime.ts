import type { KeyboardEvent as ReactKeyboardEvent } from 'react';

/**
 * True while an input method (Korean, Japanese, Chinese, ...) is composing text.
 * The Enter that commits a composition must not also submit or select. Chromium
 * flags it with `isComposing`; some macOS IMEs only report keyCode 229.
 */
export function isImeComposing(event: KeyboardEvent | ReactKeyboardEvent): boolean {
  const native = 'nativeEvent' in event ? event.nativeEvent : event;
  return native.isComposing || native.keyCode === 229;
}
