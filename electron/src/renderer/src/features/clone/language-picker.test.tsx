import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import '@/i18n';
import { LanguagePicker } from './language-picker';

vi.mock('@/lib/languages', () => ({
  LANGUAGES: ['Auto', 'English', 'Japanese'],
  POPULAR_LANGUAGES: ['English'],
}));
const select = vi.hoisted(() => vi.fn());
vi.mock('@/lib/store/clone-settings', () => ({
  useCloneSetting: () => 'Auto',
  setCloneSetting: select,
}));

it('shows language options after the popover mounts and supports filtered selection', async () => {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(320);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(340);
  render(<LanguagePicker />);
  fireEvent.click(screen.getByRole('button', { name: 'Language' }));
  expect((await screen.findAllByRole('option', { name: 'English' })).length).toBeGreaterThan(0);
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Japanese' } });
  fireEvent.click(await screen.findByRole('option', { name: 'Japanese' }));
  expect(select).toHaveBeenCalledWith('language', 'Japanese');
});

it('disables unsupported languages for pointer and keyboard selection', async () => {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(320);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(340);
  const onValueChange = vi.fn();
  render(<LanguagePicker supportedOptions={['english']} onValueChange={onValueChange} />);
  fireEvent.click(screen.getByRole('button', { name: 'Language' }));
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Japanese' } });
  const japanese = await screen.findByRole('option', { name: 'Japanese' });
  expect(japanese).toBeDisabled();
  await waitFor(() =>
    expect(screen.queryByRole('option', { name: 'English' })).not.toBeInTheDocument(),
  );
  fireEvent.click(japanese);
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
  expect(onValueChange).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'English' } });
  const english = await screen.findByRole('option', { name: 'English' });
  // The debounced rows render before the effect updates keyboard selection.
  await waitFor(() =>
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-activedescendant', english.id),
  );
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
  expect(onValueChange).toHaveBeenCalledWith('English');
});

it('resets keyboard selection when the engine changes while open', async () => {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(320);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(340);
  const onValueChange = vi.fn();
  const { rerender } = render(
    <LanguagePicker supportedOptions={['english', 'japanese']} onValueChange={onValueChange} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Language' }));
  await screen.findAllByRole('option', { name: 'English' });
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' });
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' });
  rerender(<LanguagePicker supportedOptions={['english']} onValueChange={onValueChange} />);
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
  expect(onValueChange).toHaveBeenCalledWith('Auto');
});

it('preserves keyboard selection when a refresh returns the same supported set', async () => {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(320);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(340);
  const onValueChange = vi.fn();
  const { rerender } = render(
    <LanguagePicker supportedOptions={['english', 'japanese']} onValueChange={onValueChange} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Language' }));
  await screen.findAllByRole('option', { name: 'English' });
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' });
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' });
  rerender(
    <LanguagePicker supportedOptions={['japanese', 'english']} onValueChange={onValueChange} />,
  );
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
  expect(onValueChange).toHaveBeenCalledWith('Japanese');
});

it('commits the current search immediately, including native names and codes', async () => {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(320);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(340);
  const onValueChange = vi.fn();
  render(
    <LanguagePicker
      options={['Auto', 'English', 'German', 'Japanese']}
      onValueChange={onValueChange}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Language' }));
  const input = await screen.findByRole('combobox');
  fireEvent.change(input, { target: { value: 'Deutsch' } });
  expect(screen.getByRole('option', { name: 'German' })).toBeInTheDocument();
  fireEvent.change(input, { target: { value: 'ja' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onValueChange).toHaveBeenCalledWith('Japanese');
});

it('ranks enabled options before disabled matches and keeps every language unique', async () => {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(320);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(340);
  render(
    <LanguagePicker options={['Japanese', 'English', 'Japanese']} supportedOptions={['english']} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Language' }));
  const rows = await screen.findAllByRole('option');
  expect(rows.map((row) => row.getAttribute('aria-label'))).toEqual(['English', 'Japanese']);
  expect(rows[1]).toHaveAttribute('aria-description', 'Not supported');
});

it('does not select while an IME composition is in progress', async () => {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(320);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(340);
  const onValueChange = vi.fn();
  render(<LanguagePicker onValueChange={onValueChange} />);
  fireEvent.click(screen.getByRole('button', { name: 'Language' }));
  await screen.findAllByRole('option', { name: 'English' });
  const search = screen.getByRole('combobox');
  fireEvent.keyDown(search, { key: 'Enter', isComposing: true });
  fireEvent.keyDown(search, { key: 'Enter', keyCode: 229 });
  expect(onValueChange).not.toHaveBeenCalled();
  fireEvent.keyDown(search, { key: 'Enter' });
  expect(onValueChange).toHaveBeenCalledTimes(1);
});
