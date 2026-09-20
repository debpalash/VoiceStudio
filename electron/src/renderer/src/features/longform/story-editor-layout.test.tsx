import { render } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

// The editor must not be a shrinkable flex item: with `min-h-0` a long script
// overflowed its box and painted over the generation progress panel and the
// footer while an audiobook rendered.

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/waveform-player', () => ({ WaveformPlayer: () => null }));
vi.mock('./story-preview', () => ({ previewStoryLine: vi.fn() }));
vi.mock('./story-stems', () => ({ StoryStems: () => null }));
vi.mock('@/components/pipeline-failure', () => ({ PipelineFailure: () => null }));
import { StoryEditor } from './story-editor';
import { blankLongformDraft } from './longform-session';

it('lets the script push later siblings down instead of overlapping them', () => {
  const draft = blankLongformDraft();
  draft.lines = [{ id: '1', character: 'narrator', text: 'Line one.', profileId: null }];
  const { container } = render(
    <StoryEditor draft={draft} profiles={[]} disabled={false} onChange={vi.fn()} />,
  );
  const root = container.querySelector('[data-slot="story-editor"]') as HTMLElement;
  expect(root).toBeInTheDocument();
  expect(root.className.split(' ')).not.toContain('min-h-0');
  expect(root.className.split(' ')).toContain('flex-1');
});
