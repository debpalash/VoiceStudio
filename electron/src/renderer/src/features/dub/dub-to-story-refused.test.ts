import { expect, it } from 'vitest';
import { loadDubIntoStories } from './dub-to-story';
import {
  clearLongformDraftForReset,
  longformSession,
  storiesImportEpoch,
} from '../longform/longform-session';

// Closing persistence is permanent for the module, so this lives in its own file.
it('returns false and keeps the import epoch when the draft store refuses', () => {
  const oldEpoch = storiesImportEpoch.current;
  clearLongformDraftForReset();
  const loaded = loadDubIntoStories([{ id: 'a', text: 'Hello there.', speaker_id: 'x' }], {
    unknownSpeakerLabel: 'Speaker',
  });
  expect(loaded).toBe(false);
  expect(storiesImportEpoch.current).toBe(oldEpoch);
  expect(longformSession.state.drafts.stories.lines).toEqual([]);
});
