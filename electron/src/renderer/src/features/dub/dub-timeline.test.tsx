import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { DubTimeline } from './dub-timeline';
import { moveResizeDubSegment } from './dub-session';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('./dub-session', () => ({ deleteDubSegment: vi.fn(), moveResizeDubSegment: vi.fn() }));
vi.mock('@/lib/audio/playback-clock', () => ({ usePlaybackClock: () => ({ duration: 1980, time: 0 }), requestPlaybackRange: vi.fn(), requestPlaybackSeek: vi.fn() }));
beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('keeps short segments proportional on long recordings and offers zoom', () => {
  render(<DubTimeline segments={[{id:'a',start:0,end:1,text:'a',text_original:'a'},{id:'b',start:2,end:3,text:'b',text_original:'b'}]}
    disabled={false} mediaDuration={1980} selectedId={null} onSelect={vi.fn()} />);
  const options = screen.getAllByRole('option');
  expect(parseFloat(options[0].style.width)).toBeCloseTo(100 / 1980, 4);
  expect(parseFloat(options[0].style.width)).toBeLessThan(parseFloat(options[1].style.left));
  fireEvent.click(screen.getByRole('button', { name: 'trimmer.zoom_in' }));
  expect(screen.getByRole('listbox').style.width).toBe('200%');
  fireEvent.click(screen.getByRole('button', { name: 'trimmer.fit_all' }));
  expect(screen.getByRole('listbox').style.width).toBe('100%');
});
it('snaps to an onset with S but leaves Mod+S and Alt+S alone', () => {
  render(<DubTimeline segments={[{id:'a',start:0.2,end:1,text:'a',text_original:'a'}]}
    disabled={false} mediaDuration={10} onsets={[0.25]} selectedId={null} onSelect={vi.fn()} />);
  const segment = screen.getByRole('option');
  fireEvent.keyDown(segment, { key: 's', metaKey: true });
  fireEvent.keyDown(segment, { key: 's', ctrlKey: true });
  fireEvent.keyDown(segment, { key: 's', altKey: true });
  expect(moveResizeDubSegment).not.toHaveBeenCalled();
  fireEvent.keyDown(segment, { key: 's' });
  expect(moveResizeDubSegment).toHaveBeenCalledWith('a', expect.objectContaining({ start: 0.25 }));
});
