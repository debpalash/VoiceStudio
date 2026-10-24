import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { BackgroundVolumeControl } from './background-volume-control';
import { restoreExportPreferences } from './dub-export';

const i18n = createInstance();
await i18n.init({ lng: 'en', resources: { en: { translation: {
  exportModal: { background_volume: 'Background volume' },
} } } });
afterEach(cleanup);

it('commits a changed background level once the gesture finishes', () => {
  const commit = vi.fn();
  render(<I18nextProvider i18n={i18n}><BackgroundVolumeControl
    value={1} disabled={false} onCommit={commit} />
  </I18nextProvider>);
  const slider = screen.getByRole('slider', { name: 'Background volume' });
  expect(slider).toHaveValue('100');
  fireEvent.change(slider, { target: { value: '200' } });
  expect(slider).toHaveAttribute('aria-valuetext', '200%');
  expect(commit).not.toHaveBeenCalled();
  fireEvent.pointerUp(slider);
  expect(commit).toHaveBeenCalledExactlyOnceWith(2);
});

it('supports keyboard commits, disabled background and restored preferences', () => {
  const saved = restoreExportPreferences(JSON.parse(JSON.stringify({ bedGain: 0, preserveBg: true })));
  const commit = vi.fn();
  const view = render(<I18nextProvider i18n={i18n}><BackgroundVolumeControl
    value={saved.bedGain!} disabled={false} onCommit={commit} />
  </I18nextProvider>);
  const slider = screen.getByRole('slider', { name: 'Background volume' });
  expect(slider).toHaveValue('0');
  fireEvent.change(slider, { target: { value: '400' } });
  fireEvent.keyUp(slider, { key: 'ArrowRight' });
  expect(commit).toHaveBeenCalledExactlyOnceWith(4);
  view.rerender(<I18nextProvider i18n={i18n}><BackgroundVolumeControl
    value={4} disabled={true} onCommit={commit} />
  </I18nextProvider>);
  expect(slider).toBeDisabled();
  fireEvent.blur(slider);
  expect(commit).toHaveBeenCalledTimes(1);
});


it('discards an uncommitted gesture when background audio is disabled', () => {
  const commit = vi.fn();
  const control = (disabled: boolean) => <I18nextProvider i18n={i18n}>
    <BackgroundVolumeControl value={1} disabled={disabled} onCommit={commit} />
  </I18nextProvider>;
  const view = render(control(false));
  const slider = screen.getByRole('slider', { name: 'Background volume' });
  fireEvent.change(slider, { target: { value: '250' } });
  expect(slider).toHaveValue('250');
  view.rerender(control(true));
  expect(slider).toHaveValue('100');
  view.rerender(control(false));
  fireEvent.blur(slider);
  expect(commit).not.toHaveBeenCalled();
});

it('does not quantize a valid fractional saved gain when focused and blurred without edits', () => {
  const commit = vi.fn();
  render(<I18nextProvider i18n={i18n}><BackgroundVolumeControl
    value={1.234} disabled={false} onCommit={commit} /></I18nextProvider>);
  const slider = screen.getByRole('slider', { name: 'Background volume' });
  expect(slider).toHaveValue('123');
  expect(slider).toHaveAttribute('step', '1');
  fireEvent.focus(slider);
  fireEvent.blur(slider);
  fireEvent.keyUp(slider, { key: 'Tab' });
  expect(commit).not.toHaveBeenCalled();
});
