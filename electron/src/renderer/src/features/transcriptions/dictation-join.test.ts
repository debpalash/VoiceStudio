import { describe, expect, it } from 'vitest';
import {
  dictationSeparator,
  joinDictationText,
  sherpaSummaryTail,
} from '@shared/utils/captureProtocol';

// CJK samples are \u-escaped to stay outside the hardcoded-CJK scan.
const NI_HAO = '\u4f60\u597d\u3002';
const SHI_JIE = '\u4e16\u754c\u3002';

describe('dictation piece joining', () => {
  it('puts one space between Latin pieces', () => {
    expect(joinDictationText(['Hello world.', ' How are you? '])).toBe('Hello world. How are you?');
    expect(dictationSeparator('Hello.', 'Bye.')).toBe(' ');
  });

  it('types CJK pieces without a space between them', () => {
    expect(joinDictationText([NI_HAO, SHI_JIE])).toBe(NI_HAO + SHI_JIE);
    expect(dictationSeparator(NI_HAO, SHI_JIE)).toBe('');
    expect(dictationSeparator('', 'First.')).toBe('');
  });

  it('skips empty pieces instead of adding separators', () => {
    expect(joinDictationText(['', 'Hi.', '  ', 'Bye.'])).toBe('Hi. Bye.');
  });

  it('finds the summary tail after CJK pieces joined without a space', () => {
    expect(sherpaSummaryTail(NI_HAO + SHI_JIE, [NI_HAO])).toBe(SHI_JIE);
    expect(sherpaSummaryTail(NI_HAO + SHI_JIE, [NI_HAO, SHI_JIE])).toBe('');
  });

  it('never splits a Latin word when finding the tail', () => {
    expect(sherpaSummaryTail('Hello there.', ['Hello'])).toBe('there.');
    expect(sherpaSummaryTail('Helloo there.', ['Hello'])).toBe('');
  });
});
