import { expect, it } from 'vitest';
import { importToText } from '@shared/utils/importStory';
import cases from '../../../../../../tests/fixtures/caption_ruby_cases.json';

function caption(cue: string, webvtt: boolean) {
  return `${webvtt ? 'WEBVTT\n\n' : '1\n'}00:00:01.000 --> 00:00:02.000\n${cue}\n`;
}

for (const webvtt of [false, true]) {
  const filename = webvtt ? 'captions.vtt' : 'captions.srt';
  it.each(cases)(`${filename}: $name`, ({ cue, spoken }) => {
    expect(importToText(filename, caption(cue, webvtt))).toBe(spoken);
  });

  it.each(['', '\n'])(
    `${filename}: keeps later cues after an unclosed annotation (gap %j)`,
    (gap) => {
      const content =
        caption('Please <ruby>read<rt>reed', webvtt) +
        gap +
        `${webvtt ? '' : '2\n'}00:00:02.000 --> 00:00:03.000\nNext cue.\n`;
      // Compact imports retain their existing single-paragraph layout.
      expect(importToText(filename, content)).toBe(`Please read${gap ? '\n' : ' '}Next cue.`);
    },
  );
}

it('preserves escaped WebVTT annotations and literal SubRip angle brackets', () => {
  expect(importToText('a.vtt', caption('&lt;rt&gt;reed&lt;/rt&gt;', true))).toBe('<rt>reed</rt>');
  expect(importToText('a.srt', caption('<rt 5> is literal in SubRip', false))).toBe(
    '<rt 5> is literal in SubRip',
  );
});

it('handles many nested annotations without losing following dialogue', () => {
  const cue =
    '<ruby>base' +
    '<rtc>'.repeat(20_000) +
    '</rp>'.repeat(20_000) +
    '</rtc>'.repeat(20_000) +
    '</ruby> end';
  expect(importToText('a.srt', caption(cue, false))).toBe('base end');
});
