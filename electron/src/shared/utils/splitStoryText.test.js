import { describe, it, expect } from 'vitest';
import {
  DEFAULT_SPLIT_MAX,
  DEFAULT_SPLIT_MODE,
  SPLIT_MODES,
  splitStoryText,
} from './splitStoryText';

describe('splitStoryText', () => {
  it('exposes the preset table', () => {
    expect(SPLIT_MODES).toEqual(['sentences', 'paragraphs', 'chapters']);
    expect(DEFAULT_SPLIT_MODE).toBe('paragraphs');
    expect(DEFAULT_SPLIT_MAX).toEqual({ sentences: 180, paragraphs: 500, chapters: 500 });
  });

  it('treats a bare "#" or "##" line as body text', () => {
    expect(splitStoryText('#\n## Sub\n\nafter', 'paragraphs')).toEqual(['#\n## Sub', 'after']);
  });

  it('handles lone CR line ends and unknown modes like sentences', () => {
    expect(splitStoryText('# T\rBody.', 'chapters')).toEqual(['# T', 'Body.']);
    expect(splitStoryText('One. Two.', 'whatever')).toEqual(['One. Two.']);
  });
});
