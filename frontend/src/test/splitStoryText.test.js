// Split presets for Paste & Split (#2217). Sentences keeps the old splitter;
// paragraphs and chapters give a single narrator coarser lines so a story is
// not read as a string of separate takes.
import { describe, it, expect } from 'vitest';
import {
  DEFAULT_SPLIT_MODE,
  SPLIT_MODES,
  splitIntoChunks,
  splitStoryText,
} from '../utils/splitStoryText';

const BOOK = [
  '# Chapter One',
  'It was late. The rain had stopped.',
  '',
  'Nobody moved. Then the door opened.',
  '# Chapter Two',
  'Morning came early.',
].join('\n');

describe('splitStoryText', () => {
  it('exposes the three presets and defaults to paragraphs', () => {
    expect(SPLIT_MODES).toEqual(['sentences', 'paragraphs', 'chapters']);
    expect(DEFAULT_SPLIT_MODE).toBe('paragraphs');
  });

  it('sentences = the sentence-aware splitter, headings kept as their own lines', () => {
    expect(splitStoryText(BOOK, 'sentences', 40)).toEqual([
      '# Chapter One',
      ...splitIntoChunks(
        'It was late. The rain had stopped.\n\nNobody moved. Then the door opened.',
        40,
      ),
      '# Chapter Two',
      'Morning came early.',
    ]);
  });

  it('paragraphs = one line per blank-line paragraph, headings separate', () => {
    expect(splitStoryText(BOOK, 'paragraphs')).toEqual([
      '# Chapter One',
      'It was late. The rain had stopped.',
      'Nobody moved. Then the door opened.',
      '# Chapter Two',
      'Morning came early.',
    ]);
  });

  it('paragraphs falls back to single newlines when the text has no blank lines', () => {
    expect(splitStoryText('One.\nTwo.\nThree.', 'paragraphs')).toEqual(['One.', 'Two.', 'Three.']);
  });

  it('chapters = one line per chapter body with paragraph breaks preserved', () => {
    expect(splitStoryText(BOOK, 'chapters')).toEqual([
      '# Chapter One',
      'It was late. The rain had stopped.\n\nNobody moved. Then the door opened.',
      '# Chapter Two',
      'Morning came early.',
    ]);
  });

  it('chapters with no headings is a single line; text before the first heading is kept', () => {
    expect(splitStoryText('Just prose.\n\nMore prose.', 'chapters')).toEqual([
      'Just prose.\n\nMore prose.',
    ]);
    expect(splitStoryText('Preface.\n# One\nBody.', 'chapters')).toEqual([
      'Preface.',
      '# One',
      'Body.',
    ]);
  });

  it('only what the renderer treats as a chapter (a non-empty H1) is a boundary', () => {
    expect(splitStoryText('# One\nBody.\n## Scene\nMore.\n#\nEnd.', 'chapters')).toEqual([
      '# One',
      'Body.\n## Scene\nMore.\n#\nEnd.',
    ]);
  });

  it('LF, CRLF and lone CR all separate lines and paragraphs', () => {
    for (const nl of ['\n', '\r\n', '\r']) {
      expect(splitStoryText(`# One${nl}A.${nl}${nl}B.`, 'paragraphs')).toEqual([
        '# One',
        'A.',
        'B.',
      ]);
    }
  });

  it('empty and whitespace-only input give no lines, CRLF is normalised', () => {
    for (const mode of SPLIT_MODES) {
      expect(splitStoryText('', mode)).toEqual([]);
      expect(splitStoryText(' \n\n ', mode)).toEqual([]);
    }
    expect(splitStoryText('A.\r\n\r\nB.', 'paragraphs')).toEqual(['A.', 'B.']);
  });
});
