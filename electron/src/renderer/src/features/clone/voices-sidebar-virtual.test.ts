import { expect, it } from 'vitest';
import { profileListPresentation, selectedVoiceId } from './saved-voice-parts';

it('virtualizes a large sidebar library while preserving the full Profiles grid', () => {
  expect(profileListPresentation(30, false)).toBe('list');
  expect(profileListPresentation(31, false)).toBe('virtual');
  expect(profileListPresentation(100, true)).toBe('grid');
});

it('marks only the current workspace voice as selected', () => {
  expect(selectedVoiceId('/clone', 'clone-1', 'design-1')).toBe('clone-1');
  expect(selectedVoiceId('/', 'clone-1', 'design-1')).toBe('clone-1');
  expect(selectedVoiceId('/design/', 'clone-1', 'design-1')).toBe('design-1');
  expect(selectedVoiceId('/design', 'clone-1', null)).toBeNull();
});
