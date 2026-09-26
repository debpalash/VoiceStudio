import { describe, expect, it, vi } from 'vitest';
import { generationFailureMessage } from './generationFailureMessage';

describe('generationFailureMessage', () => {
  it.each([
    null,
    'GPU_ARCH_UNSUPPORTED',
    {},
    { docs_topic: 'constructor' },
    { docs_topic: 'tts_errors.gpu_arch_unsupported' },
    { docs_topic: {} },
  ])('ignores unknown or malformed topics: %j', (value) => {
    const translate = vi.fn();
    expect(generationFailureMessage(value, translate)).toBeUndefined();
    expect(translate).not.toHaveBeenCalled();
  });
});
