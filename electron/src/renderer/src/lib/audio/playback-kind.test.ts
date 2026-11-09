import { describe, expect, it } from 'vitest';
import en from '@/i18n/locales/en.json';
import {
  clipTitle,
  playbackKindLabelKey,
  takePlaybackKind,
  type PlaybackKind,
} from './playback-kind';

function label(key: string): unknown {
  return key
    .split('.')
    .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
}

describe('takePlaybackKind', () => {
  it('names each history mode after the workspace that produced it', () => {
    expect(takePlaybackKind('design')).toBe('design');
    expect(takePlaybackKind('clone')).toBe('clone');
    expect(takePlaybackKind('convert')).toBe('convert');
    expect(takePlaybackKind(null)).toBe('clone');
    expect(takePlaybackKind('')).toBe('clone');
    expect(takePlaybackKind('something-new')).toBe('audio');
  });
});

describe('playbackKindLabelKey', () => {
  const kinds: PlaybackKind[] = [
    'clone',
    'design',
    'convert',
    'dub',
    'audiobook',
    'story',
    'audio',
  ];

  it('maps every kind to an existing, distinct English label', () => {
    const labels = kinds.map((kind) => label(playbackKindLabelKey(kind)));
    for (const text of labels) expect(typeof text).toBe('string');
    expect(new Set(labels).size).toBe(kinds.length);
    expect(label(playbackKindLabelKey('design'))).toBe('Voice design');
    expect(label(playbackKindLabelKey('convert'))).toBe('Voice conversion');
  });
});

describe('clipTitle', () => {
  it('keeps short titles whole', () => {
    expect(clipTitle('Welcome aboard.')).toBe('Welcome aboard.');
  });

  it('cuts long titles on a word boundary with an ellipsis', () => {
    const text =
      "Welcome aboard. I was just a three-second clip a moment ago — now I can say anything you'd like.";
    const short = clipTitle(text, 40);
    expect(short).toBe('Welcome aboard. I was just a…');
    expect(short.length).toBeLessThanOrEqual(41);
  });

  it('cuts a single overlong word at the limit', () => {
    expect(clipTitle('x'.repeat(80), 10)).toBe('x'.repeat(10) + '…');
  });
});
