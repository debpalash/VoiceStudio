import { describe, it, expect } from 'vitest';
import { parseSrt, importToText } from './importStory';

const SRT = `1
00:00:01,000 --> 00:00:03,000
Hello there.

2
00:00:03,500 --> 00:00:05,000
How are you?
`;

describe('parseSrt', () => {
  it('strips indices + timestamps, one cue per line', () => {
    expect(parseSrt(SRT)).toBe('Hello there.\nHow are you?');
  });
  it('joins multi-line cues with a space', () => {
    const s = '1\n00:00:01,000 --> 00:00:02,000\nLine one\nline two\n';
    expect(parseSrt(s)).toBe('Line one line two');
  });
  it('empty → empty', () => {
    expect(parseSrt('')).toBe('');
  });
  it('keeps a cue whose dialogue is only a number', () => {
    const s =
      '1\n00:00:01,000 --> 00:00:02,000\nCount down with me.\n\n' +
      '2\n00:00:02,000 --> 00:00:03,000\n3\n\n' +
      '3\n00:00:03,000 --> 00:00:04,000\n2\n1\n';
    expect(parseSrt(s)).toBe('Count down with me.\n3\n2 1');
  });
  it('keeps a numeric cue in a compact file whose next cue has no index', () => {
    const s =
      '00:00:01,000 --> 00:00:02,000\nCount down with me.\n' +
      '00:00:02,000 --> 00:00:03,000\n3\n' +
      '00:00:03,000 --> 00:00:04,000\n2\n';
    expect(parseSrt(s)).toBe('Count down with me. 3 2');
  });
  it('keeps numeric dialogue at the end of a multi-line cue in a compact file', () => {
    const s =
      '00:00:01,000 --> 00:00:02,000\nCount down with me.\n3\n' +
      '00:00:02,000 --> 00:00:03,000\nNext\n';
    expect(parseSrt(s)).toBe('Count down with me. 3 Next');
  });
  it('preserves dialogue equal to the next index when cues are unnumbered', () => {
    expect(
      parseSrt('00:00:01,000 --> 00:00:02,000\nCount\n2\n00:00:02,000 --> 00:00:03,000\nNext'),
    ).toBe('Count 2 Next');
  });
  it('still drops the indices of a compact file', () => {
    const s =
      '1\n00:00:01,000 --> 00:00:02,000\nCount\n' +
      '2\n00:00:02,000 --> 00:00:03,000\n3\n' +
      '3\n00:00:03,000 --> 00:00:04,000\n2\n';
    expect(parseSrt(s)).toBe('Count 3 2');
  });
  it('drops karaoke tags and alignment overrides from spoken lines', () => {
    const s =
      '1\n00:00:01,000 --> 00:00:02,000\n{\\an8}{\\i1}Hello{\\i0}\n\n' +
      '2\n00:00:02,000 --> 00:00:03,000\nhey<00:00:00.480><c> everyone</c>\n';
    expect(parseSrt(s)).toBe('Hello\nhey everyone');
  });
  it('keeps a literal less-than that is not a tag', () => {
    expect(parseSrt('1\n00:00:01,000 --> 00:00:02,000\nI <3 you\n')).toBe('I <3 you');
  });
  it('keeps angle-bracketed dialogue that is not a player tag', () => {
    for (const line of ['2 < 3 and 4 > 1', '<laughter> okay', 'if a<b and c>d']) {
      expect(parseSrt(`1\n00:00:01,000 --> 00:00:02,000\n${line}\n`)).toBe(line);
    }
  });
  it('drops font, voice and bold tags but keeps CJK and RTL words', () => {
    const words = '\u4f60\u597d \u05e9\u05dc\u05d5\u05dd';
    const s = `1\n00:00:01,000 --> 00:00:02,000\n<font color="#ff0">{\\an8}<b>${words}</b></font> <v Roger>ok</v>\n`;
    expect(parseSrt(s)).toBe(`${words} ok`);
  });
  it('reads a SubRip line break as a word gap', () => {
    expect(parseSrt('1\n00:00:01,000 --> 00:00:02,000\nHello<br>big<BR />world\n')).toBe(
      'Hello big world',
    );
  });
  it('scans unclosed markup prefixes in linear time', () => {
    const started = Date.now();
    parseSrt(`1\n00:00:01,000 --> 00:00:02,000\n${'<i'.repeat(50000)}${'{\\'.repeat(50000)}\n`);
    parseSrt(`WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n${'<'.repeat(50000)}\n`);
    expect(Date.now() - started).toBeLessThan(1000);
  });
  it('does not speak a WebVTT file header', () => {
    const vtt =
      'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n\n00:00:02.000 --> 00:00:03.000\nWorld\n';
    expect(parseSrt(vtt)).toBe('Hello\nWorld');
  });
  it('skips WebVTT timings with three-digit hours instead of speaking them', () => {
    expect(parseSrt('WEBVTT\n\n100:00:00.000 --> 100:00:02.000\nLong recording\n')).toBe(
      'Long recording',
    );
  });
  it('drops NOTE, STYLE and REGION blocks and cue identifiers', () => {
    const vtt =
      'WEBVTT\n\nNOTE made by a translator\n\n' +
      'intro\n00:00:01.000 --> 00:00:02.500 align:start\nHola\n\n' +
      'STYLE\n::cue { color: red }\n\n' +
      'REGION\nid:fred width:40%\n\n' +
      'cue-2\n00:00:03.000 --> 00:00:04.000\nQue tal\n';
    expect(parseSrt(vtt)).toBe('Hola\nQue tal');
  });
  it('does not speak arrow-bearing WebVTT metadata without a timing line', () => {
    const vtt =
      'WEBVTT\n\nNOTE\nTransition A --> B\nprivate note\n\n' +
      'STYLE\nTransition A --> B\n::cue { color: red }\n\n' +
      '00:00:01.000 --> 00:00:02.000\nSpoken text\n';
    expect(parseSrt(vtt)).toBe('Spoken text');
  });
  it('never treats a NOTE block as a cue even when it contains a valid timing line', () => {
    const vtt =
      'WEBVTT\n\nNOTE\n00:00:01.000 --> 00:00:02.000\nprivate note\n\n' +
      '00:00:03.000 --> 00:00:04.000\nSpoken text\n';
    expect(parseSrt(vtt)).toBe('Spoken text');
  });
  it('keeps NOTE when it is the spoken dialogue', () => {
    expect(
      parseSrt('WEBVTT\n\n00:01.000 --> 00:02.000\nNOTE this is spoken\nSTYLE\nREGION\n'),
    ).toBe('NOTE this is spoken STYLE REGION');
  });
  it('unescapes WebVTT character references after dropping tags', () => {
    expect(parseSrt('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nTom &amp; Jerry\n')).toBe(
      'Tom & Jerry',
    );
    expect(
      parseSrt('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n&lt;i&gt;literal&lt;/i&gt; <i>real</i>\n'),
    ).toBe('<i>literal</i> real');
  });
  it('decodes other HTML references and directional marks in WebVTT speech', () => {
    expect(parseSrt('WEBVTT\n\n00:01.000 --> 00:02.000\nFran&ccedil;ais &lrm;gauche&rlm;\n')).toBe(
      'Français \u200egauche\u200f',
    );
  });
  it('still keeps a SubRip entity as written', () => {
    expect(parseSrt('1\n00:00:01,000 --> 00:00:02,000\nTom &amp; Jerry\n')).toBe('Tom &amp; Jerry');
  });
  it('reads a caption file that uses only CR line endings', () => {
    expect(
      parseSrt(
        '1\r00:00:01,000 --> 00:00:02,000\rHello\r\r2\r00:00:02,000 --> 00:00:03,000\rWorld',
      ),
    ).toBe('Hello\nWorld');
  });
});

describe('importToText', () => {
  it('routes .srt through parseSrt', () => {
    expect(importToText('subs.srt', SRT)).toBe('Hello there.\nHow are you?');
  });
  it('passes .txt through unchanged', () => {
    expect(importToText('story.txt', 'Once upon a time.')).toBe('Once upon a time.');
  });
  it('routes .vtt through parseSrt so timestamps are not spoken', () => {
    const vtt =
      'WEBVTT\n\n00:00:00.160 --> 00:00:02.310 align:start position:0%\nhey<00:00:00.480><c> everyone</c>\n';
    expect(importToText('captions.vtt', vtt)).toBe('hey everyone');
    expect(importToText('captions.VTT', vtt)).toBe('hey everyone');
  });
});
