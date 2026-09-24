import { expect, it } from 'vitest';
import { importToText } from '../../../../../../frontend/src/utils/importStory';
import {
  splitIntoChunks,
  splitStoryText,
} from '../../../../../../frontend/src/utils/splitStoryText';
it('removes SRT metadata and retains spoken lines', () => {
  expect(
    importToText(
      'captions.SRT',
      '1\n00:00:01,000 --> 00:00:02,000\nFirst cue\n\n2\n00:00:02,000 --> 00:00:03,000\nSecond cue',
    ),
  ).toBe('First cue\nSecond cue');
});
it('splits locally with bounded chunks and stable text order', () => {
  const text = 'This is the first sentence of the story. This is the second sentence of the story.';
  const chunks = splitIntoChunks(text, 40);
  expect(chunks.every((chunk) => chunk.length <= 40)).toBe(true);
  expect(chunks.join(' ')).toBe(text);
  expect(splitIntoChunks('', 500)).toEqual([]);
});
it('split presets give one line per paragraph or per chapter, headings kept separate', () => {
  const book = '# One\nFirst para.\n\nSecond para.\n# Two\nLast.';
  expect(splitStoryText(book, 'paragraphs')).toEqual([
    '# One',
    'First para.',
    'Second para.',
    '# Two',
    'Last.',
  ]);
  expect(splitStoryText(book, 'chapters')).toEqual([
    '# One',
    'First para.\n\nSecond para.',
    '# Two',
    'Last.',
  ]);
  expect(splitStoryText(book, 'sentences', 500)).toEqual([
    '# One',
    'First para.\n\nSecond para.',
    '# Two',
    'Last.',
  ]);
});

it('decodes a UTF-16 manuscript before extracting subtitle speech', async () => {
  const { readTextFile } = await import('../../../../../../frontend/src/utils/readTextFile');
  const text = '1\n00:00:01,000 --> 00:00:02,000\nCafé — hello';
  const bytes = new Uint8Array(2 + text.length * 2);
  bytes.set([0xff, 0xfe]);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < text.length; i++) view.setUint16(2 + i * 2, text.charCodeAt(i), true);
  const file = { arrayBuffer: async () => bytes.buffer };
  expect(importToText('captions.srt', await readTextFile(file))).toBe('Café — hello');
});

it('retains numeric dialogue through the Electron manuscript importer', () => {
  const text = importToText(
    'countdown.srt',
    '1\n00:00:01,000 --> 00:00:02,000\n3\n\n2\n00:00:02,000 --> 00:00:03,000\n2\n\n3\n00:00:03,000 --> 00:00:04,000\n1',
  );
  expect(text).toBe('3\n2\n1');
  expect(splitIntoChunks(text, 100).join(' ')).toMatch(/3\s+2\s+1/);
});
it('drops caption markup from an imported story SRT', () => {
  expect(
    importToText(
      'fansub.srt',
      '1\n00:00:01,000 --> 00:00:02,000\n{\\an8}<i>Hello</i>\n\n2\n00:00:02,000 --> 00:00:03,000\nhey<00:00:00.480><c> everyone</c>\n',
    ),
  ).toBe('Hello\nhey everyone');
});
it('drops WebVTT scaffolding from an imported story caption file', () => {
  const vtt =
    'WEBVTT\n\nNOTE translator notes\n\nintro\n00:00:00.160 --> 00:00:02.310 align:start position:0%\nhey<00:00:00.480><c> everyone</c>\n';
  expect(importToText('captions.vtt', vtt)).toBe('hey everyone');
  expect(importToText('captions.srt', vtt)).toBe('hey everyone');
});
