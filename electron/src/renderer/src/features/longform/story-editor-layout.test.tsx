import { render } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import '@/i18n';

vi.mock('@/components/waveform-player', () => ({ WaveformPlayer: () => null }));
vi.mock('./story-preview', () => ({ previewStoryLine: vi.fn() }));
vi.mock('./story-stems', () => ({ StoryStems: () => null }));
vi.mock('@/components/pipeline-failure', () => ({ PipelineFailure: () => null }));

import { StoryEditor } from './story-editor';
import { blankLongformDraft } from './longform-session';

it('lets the editor grow inside the page scroller instead of clipping to its own box', () => {
  const draft = blankLongformDraft();
  draft.lines = [{ id: 'only', text: 'A single line of story.', profileId: null }];
  const { container } = render(
    <StoryEditor draft={draft} profiles={[]} disabled={false} onChange={() => {}} />,
  );
  const editor = container.querySelector('[data-slot="story-editor"]');
  expect(editor).not.toBeNull();
  expect(editor).toHaveClass('flex-1');
  expect(editor).not.toHaveClass('min-h-0');
});
