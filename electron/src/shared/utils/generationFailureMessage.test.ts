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
    ['transcription_disk_full', 'tts_errors.disk_full'],
  ])('localizes the %s transcription failure code', (code, key) => {
    const translate = vi.fn((k: string) => `t:${k}`);
    expect(generationFailureMessage({ code }, translate)).toBe(`t:${key}`);
  });

  it.each([
    ['GPU_OOM', 'tts_errors.gpu_oom'],
    ['HOST_MEMORY_EXHAUSTED', 'tts_errors.host_memory_exhausted'],
    ['DISK_FULL', 'tts_errors.disk_full'],
    ['ENGINE_DEPENDENCY_MISSING', 'tts_errors.engine_dependency_missing'],
    ['REFERENCE_STT_MISSING', 'tts_errors.reference_stt_missing'],
    ['SOURCE_LANGUAGE_REQUIRED', 'tts_errors.source_language_required'],
    ['MEDIA_TOOL_MISSING', 'failureClass.media_tool_missing'],
    ['HF_MIRROR_UNREACHABLE', 'failureClass.hf_mirror_unreachable'],
    ['VIDEO_DOWNLOAD_BOT_CHECK', 'failureClass.video_download_bot_check'],
  ])('localizes the %s diagnostic topic instead of the generic floor message', (topic, key) => {
    const translate = vi.fn((k: string) => `t:${k}`);
    expect(generationFailureMessage({ docs_topic: topic }, translate)).toBe(`t:${key}`);
  });

  it('never turns a server-supplied code into a translation key', () => {
    const translate = vi.fn();
    expect(
      generationFailureMessage({ code: 'tts_errors.no_audio_track' }, translate),
    ).toBeUndefined();
    expect(translate).not.toHaveBeenCalled();
  });
});
