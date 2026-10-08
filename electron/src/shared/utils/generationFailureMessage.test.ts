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

  it.each([
    ['transcription_media_tool', 'tts_errors.transcription_media_tool'],
    ['transcription_pipe_lost', 'tts_errors.transcription_pipe_lost'],
  ])('localizes the %s transcription failure code', (code, key) => {
    const translate = vi.fn((k: string) => `t:${k}`);
    expect(generationFailureMessage({ code }, translate)).toBe(`t:${key}`);
  });

  it('never turns a server-supplied code into a translation key', () => {
    const translate = vi.fn();
    expect(generationFailureMessage({ code: 'tts_errors.no_audio_track' }, translate)).toBeUndefined();
    expect(translate).not.toHaveBeenCalled();
  });
});
