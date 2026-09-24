import { expect, it } from 'vitest';
import type { Profile } from '@/lib/api/types';
import { callVoices } from './voices';

const profile = (id: string, kind: Profile['kind'], verified?: boolean | number | null) =>
  ({ id, name: id, kind, verified_own_voice: verified }) as Profile;

it('offers only voices verified as the user own voice, or designed voices', () => {
  const voices = callVoices([
    profile('mine', 'clone', true),
    profile('mine-sqlite', 'clone', 1),
    profile('someone-else', 'clone', false),
    profile('unverified', 'clone', null),
    profile('legacy', 'clone'),
    profile('designed', 'design'),
  ]);
  expect(voices.map((voice) => voice.id)).toEqual(['mine', 'mine-sqlite', 'designed']);
  expect(callVoices(undefined)).toEqual([]);
});
