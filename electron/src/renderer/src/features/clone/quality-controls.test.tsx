import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { QualityControls } from './quality-controls';
import { cloneSettingsStore, DEFAULT_CLONE_SETTINGS } from '@/lib/store/clone-settings';

const engine = vi.hoisted(() => ({ id: 'omnivoice', output_sample_rate: 24000 as number | null, output_channels: 1 as number | null }));
vi.mock('@/hooks/use-engines', () => ({ useEngines: () => ({ activeTts: engine }) }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: { size?: string }) => (params?.size ? `${key}: ${params.size}` : key),
  }),
}));

beforeEach(() => {
  // Base UI measures the track before exposing its keyboard input. jsdom has
  // no layout, so supply a real track size rather than bypassing accessibility.
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: 300,
    bottom: 16,
    width: 300,
    height: 16,
    toJSON() {},
  });
  cloneSettingsStore.setState(() => ({ ...DEFAULT_CLONE_SETTINGS }));
  engine.id = 'omnivoice';
  engine.output_sample_rate = 24000;
  engine.output_channels = 1;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const open = () =>
  fireEvent.click(screen.getByRole('button', { name: /^cloneQuality\.title: / }));

it('resets only audio quality from its header icon', () => {
  const before = { ...DEFAULT_CLONE_SETTINGS, wavBits: 32 as const, effectPreset: 'raw' as const,
    steps: 64, speed: 1.5, duration: '3', denoise: false, text: 'Keep this', language: 'French' };
  cloneSettingsStore.setState(() => before);
  render(<QualityControls />);
  open();
  const reset = screen.getByRole('button', { name: 'clone.reset_overrides' });
  expect(reset.textContent).toBe('');
  expect(reset.querySelector('svg')).not.toBeNull();
  fireEvent.click(reset);
  expect(cloneSettingsStore.state).toEqual({ ...before,
    wavBits: DEFAULT_CLONE_SETTINGS.wavBits,
    steps: DEFAULT_CLONE_SETTINGS.steps,
    effectPreset: DEFAULT_CLONE_SETTINGS.effectPreset });
  expect(screen.getByRole('radio', { name: /cloneQuality\.preset16/ })).toHaveAttribute('aria-checked', 'true');
});

it('disables audio-quality reset while generating', () => {
  cloneSettingsStore.setState(() => ({ ...DEFAULT_CLONE_SETTINGS, wavBits: 32 }));
  render(<QualityControls disabled />);
  open();
  const reset = screen.getByRole('button', { name: 'clone.reset_overrides' });
  expect(reset).toBeDisabled();
  fireEvent.click(reset);
  expect(cloneSettingsStore.state.wavBits).toBe(32);
});

it('shows the current preset on the composer pill and keeps tuning closed until opened', () => {
  render(<QualityControls />);
  expect(screen.getByRole('button', { name: 'cloneQuality.title: cloneQuality.preset16' })).toBeVisible();
  expect(screen.queryByRole('radiogroup')).toBeNull();
  expect(screen.queryByRole('switch')).toBeNull();
  open();
  expect(screen.getByRole('radiogroup', { name: 'cloneQuality.title' })).toBeInTheDocument();
  expect(screen.getByRole('switch', { name: 'cloneQuality.mastering' })).toBeInTheDocument();
});

it('changes export precision with the keyboard, updates size and keeps sampling independent', async () => {
  render(<QualityControls />);
  open();
  const standard = await screen.findByRole('radio', { name: /cloneQuality\.preset16/ });
  expect(standard).toHaveAttribute('aria-checked', 'true');
  expect(screen.getByText('cloneQuality.size: 2.9')).toBeInTheDocument();
  fireEvent.keyDown(standard, { key: 'ArrowRight' });
  expect(cloneSettingsStore.state.wavBits).toBe(24);
  expect(screen.getByText('cloneQuality.size: 4.3')).toBeInTheDocument();
  expect(screen.getByRole('radio', { name: /cloneQuality\.preset24/ })).toHaveFocus();
  fireEvent.keyDown(standard, { key: 'End' });
  expect(cloneSettingsStore.state.wavBits).toBe(32);
  expect(screen.getByText('cloneQuality.size: 5.8')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('radio', { name: /cloneQuality\.preset16/ }));
  expect(cloneSettingsStore.state.wavBits).toBe(16);
  expect(cloneSettingsStore.state.steps).toBe(16);
  await act(async () => {
    fireEvent.keyDown(screen.getByRole('slider', { name: 'cloneQuality.effort' }), {
      key: 'ArrowRight',
    });
  });
  expect(cloneSettingsStore.state.steps).toBe(17);
  expect(cloneSettingsStore.state.wavBits).toBe(16);
  fireEvent.click(screen.getByRole('switch', { name: 'cloneQuality.mastering' }));
  expect(cloneSettingsStore.state.effectPreset).toBe('raw');
});

it('hides unsupported sampling controls and disables quality changes while generating', () => {
  engine.id = 'kittentts';
  render(<QualityControls disabled />);
  open();
  expect(screen.queryByRole('slider', { name: 'cloneQuality.effort' })).toBeNull();
  for (const radio of screen.getAllByRole('radio')) expect(radio).toBeDisabled();
  fireEvent.keyDown(screen.getByRole('radiogroup'), { key: 'End' });
  expect(cloneSettingsStore.state.wavBits).toBe(16);
  expect(screen.getByRole('switch')).toHaveAttribute('aria-disabled', 'true');
});

it.each(['omnivoice-subprocess', 'voxcpm2', 'dots-tts', 'supertonic3'])(
  'offers only supported sampling ranges for %s',
  async (id) => {
    engine.id = id;
    render(<QualityControls />);
    open();
    const steps = await screen.findByRole('slider', { name: 'cloneQuality.effort' });
    expect(steps).toHaveAttribute('max', id === 'supertonic3' ? '12' : '64');
    await act(async () => {
      fireEvent.keyDown(steps, { key: 'Home' });
    });
    await act(async () => {
      fireEvent.keyDown(steps, { key: 'End' });
    });
    expect(cloneSettingsStore.state.steps).toBe(id === 'supertonic3' ? 12 : 64);
  },
);

it('estimates from output metadata and leaves unknown model formats unspecified', () => {
  engine.id = 'voxcpm2';
  engine.output_sample_rate = 48000;
  const view = render(<QualityControls />);
  open();
  expect(screen.getByText('cloneQuality.size: 5.8')).toBeInTheDocument();
  engine.output_channels = 2;
  view.rerender(<QualityControls />);
  expect(screen.getByText('cloneQuality.size: 11.5')).toBeInTheDocument();
  engine.output_sample_rate = null;
  view.rerender(<QualityControls />);
  expect(screen.getByText('cloneQuality.sizeUnknown')).toBeInTheDocument();
});
