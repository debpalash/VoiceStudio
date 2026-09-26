import { describe, expect, it } from 'vitest';
import { engineDisplayName } from './engineDisplayName';

describe('engineDisplayName', () => {
  it('uses the model name for legacy engine and resident labels', () => {
    expect(engineDisplayName('VoiceStudio (k2-fsa/OmniVoice, 600+ languages)')).toBe(
      'OmniVoice (k2-fsa/OmniVoice, 600+ languages)',
    );
    expect(engineDisplayName('VoiceStudio TTS')).toBe('OmniVoice TTS');
    expect(engineDisplayName('KittenTTS (English)')).toBe('KittenTTS (English)');
  });
});
