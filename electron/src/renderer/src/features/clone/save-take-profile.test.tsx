import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ metadata: [] as unknown[] }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('@/lib/api/client', () => ({
  // A plain stub: a jsdom Blob inside a Node Response cannot be read back.
  apiFetch: () =>
    Promise.resolve({ blob: () => Promise.resolve(new Blob(['RIFF'], { type: 'audio/wav' })) }),
}));
vi.mock('./reference-panel', () => ({
  SaveProfileForm: ({ metadata }: { metadata: unknown }) => {
    mock.metadata.push(metadata);
    return <div>form</div>;
  },
}));

import { SaveTakeProfile } from './save-take-profile';

const LONG = 'The lighthouse keeper climbed the stairs. [pause 1s] '.repeat(5);

// #3062: a take's text may be cut short or carry script markup it never spoke;
// as a voice transcript either one is read out at every chunk start.
it.each([
  ['the server transcript', { transcript: 'Clean words.' }, 'Clean words.'],
  ['nothing when the take may be cut', { transcript: '' }, ''],
  ['nothing from an older server', {}, ''],
])('saves a take as a voice with %s', async (_label, patch, refText) => {
  mock.metadata.length = 0;
  render(
    <SaveTakeProfile
      item={{ id: 't1', text: LONG, audio_path: 't1.wav', language: 'English', ...patch } as never}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'clone.save_as_profile' }));
  expect(await screen.findByText('form')).toBeTruthy();
  expect(mock.metadata.at(-1)).toMatchObject({ refText });
});
