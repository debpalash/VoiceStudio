import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { DubTimeline } from './dub-timeline';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('./dub-session', () => ({ deleteDubSegment: vi.fn(), moveResizeDubSegment: vi.fn() }));
vi.mock('@/lib/audio/playback-clock', () => ({ usePlaybackClock: () => ({ duration: 1980, time: 0 }), requestPlaybackRange: vi.fn(), requestPlaybackSeek: vi.fn() }));
beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('keeps short segments proportional on long recordings and offers zoom', () => {
  render(<DubTimeline segments={[{id:'a',start:0,end:1,text:'a'},{id:'b',start:2,end:3,text:'b'}]}
    disabled={false} mediaDuration={1980} selectedId={null} onSelect={vi.fn()} />);
  const options = screen.getAllByRole('option');
  expect(parseFloat(options[0].style.width)).toBeCloseTo(100 / 1980, 4);
  expect(parseFloat(options[0].style.width)).toBeLessThan(parseFloat(options[1].style.left));
  fireEvent.click(screen.getByRole('button', { name: 'trimmer.zoom_in' }));
  expect(screen.getByRole('listbox').style.width).toBe('200%');
  fireEvent.click(screen.getByRole('button', { name: 'trimmer.fit_all' }));
  expect(screen.getByRole('listbox').style.width).toBe('100%');
});
