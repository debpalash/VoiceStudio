import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import '@/i18n';
import { EngineLanguagePicker } from './engine-language-picker';
const state = vi.hoisted(() => ({ names: ['english'] as string[] | null }));
vi.mock('@/hooks/use-engines', () => ({
  useEngines: () => ({ activeTts: { supported_language_names: state.names } }),
}));
vi.mock('@/lib/languages', () => ({
  LANGUAGES: ['Auto', 'English', 'Japanese'],
  POPULAR_LANGUAGES: ['English'],
}));
vi.mock('@/lib/store/clone-settings', () => ({ useCloneSetting: () => 'Japanese' }));
it('updates allowed choices after an engine switch without rewriting the saved language', async () => {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(320);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(340);
  const onValueChange = vi.fn();
  const view = render(<EngineLanguagePicker onValueChange={onValueChange} />);
  fireEvent.click(screen.getByRole('button', { name: 'Language' }));
  // The saved unsupported choice stays visible rather than hiding behind "Show all languages".
  expect(await screen.findByRole('option', { name: 'Japanese' })).toBeDisabled();
  // A toggle that cannot hide the saved choice would do nothing, so none is offered.
  expect(screen.queryByRole('option', { name: 'Hide unsupported languages' })).toBeNull();
  expect(screen.getByRole('option', { name: 'Auto' })).toBeEnabled();
  state.names = null;
  view.rerender(<EngineLanguagePicker onValueChange={onValueChange} />);
  expect(screen.getByRole('option', { name: 'Japanese' })).toBeEnabled();
  expect(screen.getByRole('option', { name: 'Japanese' })).toHaveAttribute('aria-selected', 'true');
  expect(onValueChange).not.toHaveBeenCalled();
});
