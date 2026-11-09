import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cloneSettingsStore, DEFAULT_CLONE_SETTINGS } from '@/lib/store/clone-settings';
import {
  QUALITY_OFFER_STORAGE_KEY,
  qualityOfferStore,
  recordTakeSpeed,
} from '@/lib/store/quality-offer';
import { recordRenderSample, speedKey } from '@/lib/audio/render-speed';
import { formatWait, QualityOffer } from './quality-offer';

const engine = vi.hoisted(() => ({ id: 'omnivoice', effective_device: 'cuda' }));
vi.mock('@/hooks/use-engines', () => ({ useEngines: () => ({ activeTts: engine }) }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'en' },
    t: (key: string, params?: Record<string, unknown>) =>
      params ? `${key} ${JSON.stringify(params)}` : key,
  }),
}));

const text = 'x'.repeat(100);
const seed = (renderSeconds: number, steps = 32) =>
  recordRenderSample(speedKey('omnivoice', 'cuda'), {
    chars: 100,
    audioSeconds: 60,
    renderSeconds,
    steps,
    at: 0,
  });
const bump = () =>
  act(() => qualityOfferStore.setState((prev) => ({ ...prev, version: prev.version + 1 })));

beforeEach(() => {
  localStorage.clear();
  cloneSettingsStore.setState(() => ({ ...DEFAULT_CLONE_SETTINGS }));
  qualityOfferStore.setState(() => ({
    muted: [],
    dismissed: [],
    lastTake: null,
    applied: null,
    version: 0,
  }));
  engine.id = 'omnivoice';
});
afterEach(cleanup);

it('stays silent without measurements or when the take is quick', () => {
  const view = render(<QualityOffer text={text} isGenerating={false} />);
  expect(view.container).toBeEmptyDOMElement();
  seed(20);
  bump();
  expect(view.container).toBeEmptyDOMElement();
});

it('offers faster settings before a slow take but changes nothing until clicked', () => {
  seed(120);
  render(<QualityOffer text={text} isGenerating={false} />);
  expect(screen.getByRole('status')).toHaveTextContent('cloneQuality.estimateBest');
  expect(screen.getByRole('status')).toHaveTextContent('"from":32,"to":8');
  expect(cloneSettingsStore.state.steps).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'cloneQuality.useFaster' }));
  expect(cloneSettingsStore.state.steps).toBe(8);
  // Now active: show what changed and how to undo it.
  expect(screen.getByRole('status')).toHaveTextContent('"from":32,"to":8');
  fireEvent.click(screen.getByRole('button', { name: 'cloneQuality.restore' }));
  expect(cloneSettingsStore.state.steps).toBeNull();
});

it('offers the next step after accepted faster settings prove slow too, keeping Restore', () => {
  seed(60);
  render(<QualityOffer text={text} isGenerating={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'cloneQuality.useFaster' }));
  expect(cloneSettingsStore.state.steps).toBe(16);
  // A much longer take at 16 steps is still slow: 16 → 8 is now worth offering.
  act(() =>
    recordTakeSpeed('omnivoice', 'cuda', {
      chars: 400,
      audioSeconds: 20,
      renderSeconds: 140,
      steps: 16,
      at: 0,
    }),
  );
  expect(screen.getByRole('status')).toHaveTextContent('"from":16,"to":8');
  expect(screen.getByRole('button', { name: 'cloneQuality.restore' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'cloneQuality.useFaster' }));
  expect(cloneSettingsStore.state.steps).toBe(8);
  // Restore still names the original best → current change.
  expect(screen.getByRole('status')).toHaveTextContent('"from":32,"to":8');
  fireEvent.click(screen.getByRole('button', { name: 'cloneQuality.restore' }));
  expect(cloneSettingsStore.state.steps).toBeNull();
});

it('hides while generating', () => {
  seed(120);
  const view = render(<QualityOffer text={text} isGenerating />);
  expect(view.container).toBeEmptyDOMElement();
});

it('suggests after a slow take with the measured time', () => {
  render(<QualityOffer text="" isGenerating={false} />);
  act(() =>
    recordTakeSpeed('omnivoice', 'cuda', {
      chars: 100,
      audioSeconds: 20,
      renderSeconds: 60,
      steps: 32,
      at: 0,
    }),
  );
  expect(screen.getByRole('status')).toHaveTextContent('cloneQuality.slowTake {"time":"60 sec"}');
  expect(screen.getByRole('status')).toHaveTextContent('"from":32,"to":16');
});

it('"Keep best quality" dismisses for the session; "Don\'t ask again" persists per engine', () => {
  seed(120);
  const view = render(<QualityOffer text={text} isGenerating={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'cloneQuality.keepBest' }));
  expect(view.container).toBeEmptyDOMElement();
  expect(cloneSettingsStore.state.steps).toBeNull();

  act(() => qualityOfferStore.setState((prev) => ({ ...prev, dismissed: [] })));
  fireEvent.click(screen.getByRole('button', { name: 'cloneQuality.dontAsk' }));
  expect(view.container).toBeEmptyDOMElement();
  expect(JSON.parse(localStorage.getItem(QUALITY_OFFER_STORAGE_KEY)!)).toEqual({
    muted: ['omnivoice'],
  });
  // Another engine still asks.
  engine.id = 'voxcpm2';
  recordRenderSample(speedKey('voxcpm2', 'cuda'), {
    chars: 100,
    audioSeconds: 60,
    renderSeconds: 120,
    steps: 32,
    at: 0,
  });
  bump();
  expect(screen.getByRole('status')).toBeInTheDocument();
});

it('says "keep these settings" when the user already chose fewer steps', () => {
  seed(240, 24);
  cloneSettingsStore.setState((prev) => ({ ...prev, steps: 24 }));
  render(<QualityOffer text={text} isGenerating={false} />);
  expect(screen.getByRole('status')).toHaveTextContent('cloneQuality.estimate {');
  expect(screen.getByRole('button', { name: 'cloneQuality.keepCurrent' })).toBeInTheDocument();
});

it('formats waits the way they are felt', () => {
  expect(formatWait(7.4, 'en')).toBe('7 sec');
  expect(formatWait(42, 'en')).toBe('40 sec');
  expect(formatWait(130, 'en')).toBe('2 min');
});
