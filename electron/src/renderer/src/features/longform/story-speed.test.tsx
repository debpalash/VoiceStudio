import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import i18n from '@/i18n';
import { StorySpeed, linesWithOwnSpeed } from './story-speed';
import { blankLongformDraft, type Line } from './longform-session';

const mk = (id: string, speed?: number | null): Line => ({
  id,
  text: `t-${id}`,
  profileId: null,
  speed,
});
const draftWith = (lines: Line[], globalSpeed = 1) => ({
  ...blankLongformDraft(),
  lines,
  globalSpeed,
});

describe('linesWithOwnSpeed', () => {
  it('ignores missing, null, zero and negative speeds', () => {
    expect(linesWithOwnSpeed([])).toBe(0);
    expect(linesWithOwnSpeed([mk('x'), mk('y', null), mk('z', 0), mk('w', -1)])).toBe(0);
    expect(linesWithOwnSpeed([mk('x', 0.6), mk('y', 1.9), mk('z')])).toBe(2);
  });
});

describe('StorySpeed', () => {
  it('shows the two-decimal readout and reports slider moves', () => {
    const spy = vi.fn();
    render(<StorySpeed draft={draftWith([mk('a')], 0.75)} disabled={false} onChange={spy} />);
    expect(screen.getByText('0.75×')).toBeInTheDocument();
    const slider = screen.getByLabelText(i18n.t('stories.global_speed'));
    expect(slider).toHaveAttribute('min', '0.5');
    expect(slider).toHaveAttribute('max', '2');
    fireEvent.change(slider, { target: { value: '1.8' } });
    expect(spy).toHaveBeenCalledWith({ globalSpeed: 1.8 });
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('clears every per-line speed from the reset button', () => {
    const spy = vi.fn();
    const lines = [mk('a', 1.4), mk('b', 0.7), mk('c')];
    render(<StorySpeed draft={draftWith(lines)} disabled={false} onChange={spy} />);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('stories.global_speed_apply') }));
    expect(spy).toHaveBeenCalledWith({ lines: lines.map((l) => ({ ...l, speed: null })) });
  });

  it('locks the controls while the page is busy', () => {
    render(<StorySpeed draft={draftWith([mk('a', 1.1)])} disabled onChange={vi.fn()} />);
    expect(screen.getByLabelText(i18n.t('stories.global_speed'))).toBeDisabled();
    expect(screen.getByRole('button')).toBeDisabled();
  });
});
