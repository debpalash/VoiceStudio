import { describe, expect, it } from 'vitest';
import { HomeIcon } from 'lucide-react';
import type { HistoryItem } from '@/lib/api/types';
import { commandMatches, recentTakes, takeLength, type CommandItem } from './command-palette-items';

const item = (label: string, keywords: string[] = [], id = 'x'): CommandItem => ({
  id,
  label,
  keywords,
  group: 'g',
  icon: HomeIcon,
  run: () => {},
});

describe('commandMatches', () => {
  it('finds pages by short names and English aliases, not only the full label', () => {
    expect(commandMatches(item('Voice cloning', ['Clone', 'clone'], 'clone'), 'clone')).toBe(true);
    expect(commandMatches(item('Models', ['models'], 'models'), 'models')).toBe(true);
    expect(commandMatches(item('VoiceStudio Pro', ['pro'], 'pro'), 'pro')).toBe(true);
    expect(commandMatches(item('Switch to dark theme', ['dark', 'light'], 'theme'), 'dark')).toBe(
      true,
    );
  });

  it('matches every typed word in any order and ignores case and accents', () => {
    const projects = item('Projects', ['new project'], 'projects');
    expect(commandMatches(projects, 'new project')).toBe(true);
    expect(commandMatches(projects, 'PROJECT new')).toBe(true);
    expect(commandMatches(item('Paramètres'), 'parametres')).toBe(true);
    expect(commandMatches(projects, 'new story')).toBe(false);
  });
});

const take = (id: string, text: string, created_at: number, mode = 'clone'): HistoryItem => ({
  id,
  text,
  mode,
  created_at,
  language: null,
  instruct: null,
  profile_id: null,
  audio_path: `${id}.wav`,
  duration_seconds: 3,
  generation_time: null,
  seed: null,
  starred: null,
});

describe('recentTakes', () => {
  it('lists one row per script, newest first, and caps the list', () => {
    const history = [
      ...Array.from({ length: 6 }, (_, index) => take(`a${index}`, 'Hello there', 100 + index)),
      take('b', 'Second line', 50),
      take('c', '[laughter] Hello  there', 300),
      take('d', 'Dub segment', 400, 'dub'),
      ...Array.from({ length: 6 }, (_, index) => take(`e${index}`, `Line ${index}`, 10 + index)),
    ];
    const picked = recentTakes(history);
    expect(picked.map((row) => row.id)).toEqual(['c', 'b', 'e5', 'e4', 'e3']);
    expect(new Set(picked.map((row) => row.text)).size).toBe(picked.length);
  });

  it('keeps Clone and Design takes of the same text apart', () => {
    const picked = recentTakes([take('a', 'Hi', 2), take('b', 'Hi', 1, 'design')]);
    expect(picked.map((row) => row.id)).toEqual(['a', 'b']);
  });
});

it('formats a take length as minutes and seconds', () => {
  expect(takeLength(65.4)).toBe('1:05');
  expect(takeLength(0)).toBe('');
  expect(takeLength(null)).toBe('');
});
