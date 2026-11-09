import { describe, expect, it } from 'vitest';
import type { EngineBackend } from '@/lib/api/types';
import { KNOWN_TAGS, engineShortName, expressionCaps } from './expression';
import { TAGS } from './languages';

const engine = (patch: Partial<EngineBackend>) =>
  ({ id: 'x', display_name: 'X', available: true, reason: null, ...patch }) as EngineBackend;

describe('expressionCaps (#3422)', () => {
  it('reads the catalogue capability when the backend reports it', () => {
    const caps = { emotion: 'direction' as const, tags: ['[breath]'] };
    expect(expressionCaps(engine({ expression_caps: caps }))).toBe(caps);
  });

  it('keeps an older backend on its previous behaviour', () => {
    expect(expressionCaps(engine({ supports_emotion: true }))).toEqual({
      emotion: 'graded',
      tags: TAGS,
    });
    expect(expressionCaps(null).emotion).toBe('none');
  });

  it('knows every shipped reaction tag, including CosyVoice breaths', () => {
    expect(KNOWN_TAGS).toEqual(expect.arrayContaining([...TAGS, '[breath]']));
  });

  it('names the engine without its catalogue details', () => {
    expect(engineShortName(engine({ display_name: 'CosyVoice 3 (9 langs, instruct)' }))).toBe(
      'CosyVoice 3',
    );
    expect(engineShortName(null)).toBe('');
  });

  it("names the bundled engine by its model, not the app's own name", () => {
    expect(
      engineShortName(engine({ display_name: 'VoiceStudio (k2-fsa/OmniVoice, 600+ languages)' })),
    ).toBe('OmniVoice');
    expect(engineShortName(engine({ display_name: 'VoiceStudio' }))).toBe('VoiceStudio');
  });
});
