import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { CastingBoard } from './casting-board';
import type { DubSegment } from './dub-session';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn() } }));

afterEach(cleanup);

const segment = (profileId: string): DubSegment =>
  ({
    id: '1',
    start: 0,
    end: 1,
    text: 'Hello',
    speaker_id: 'Speaker 1',
    profile_id: profileId,
  }) as DubSegment;

it('offers the source-extracted Auto voice after a speaker was recast (#3582)', async () => {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(320);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(340);
  const onAssign = vi.fn();
  render(
    <CastingBoard
      segments={[segment('preset:narrator')]}
      profiles={[]}
      disabled={false}
      onAssign={onAssign}
    />,
  );
  fireEvent.click(screen.getByRole('combobox', { name: 'dub.casting_assign_to' }));
  expect(await screen.findByRole('option', { name: 'dub.auto · Speaker 1' })).toBeTruthy();
  // Choosing it goes through the same assignment every input path uses.
  const row = document.querySelector('[data-speaker="Speaker 1"]') as HTMLElement;
  fireEvent.drop(row, { dataTransfer: { getData: () => 'auto:speaker_1' } });
  expect(onAssign).toHaveBeenCalledWith('Speaker 1', 'auto:speaker_1');
});

it('shows Auto as the current voice while the extracted voice is assigned', () => {
  render(
    <CastingBoard
      segments={[segment('auto:speaker_1')]}
      profiles={[]}
      disabled={false}
      onAssign={vi.fn()}
    />,
  );
  expect(screen.getAllByText('dub.auto · Speaker 1').length).toBeGreaterThan(0);
});

it('shows Default, not Auto, for a voice that is no longer available', () => {
  render(
    <CastingBoard
      segments={[segment('deleted-profile')]}
      profiles={[]}
      disabled={false}
      onAssign={vi.fn()}
    />,
  );
  expect(screen.getAllByText('dub.default').length).toBeGreaterThan(0);
  expect(screen.queryByText('dub.auto · Speaker 1')).toBeNull();
});
