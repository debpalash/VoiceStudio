export function keyEventToAccelerator(
  event: Pick<KeyboardEvent, 'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'code'>,
): string | null;
export function isPureModifierEvent(event: Pick<KeyboardEvent, 'key'>): boolean;
