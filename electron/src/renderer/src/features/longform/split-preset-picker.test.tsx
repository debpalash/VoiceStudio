import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import { SplitPresetPicker } from './split-preset-picker';

it('lets the character limit be typed digit by digit and clamps only on commit', () => {
  const onCharLimit = vi.fn();
  render(
    <SplitPresetPicker
      preset="sentences"
      onPreset={vi.fn()}
      charLimit={300}
      onCharLimit={onCharLimit}
    />,
  );
  const field = screen.getByRole('spinbutton') as HTMLInputElement;
  fireEvent.change(field, { target: { value: '5' } });
  expect(field.value).toBe('5');
  expect(onCharLimit).not.toHaveBeenCalled();
  fireEvent.change(field, { target: { value: '500' } });
  fireEvent.blur(field);
  expect(onCharLimit).toHaveBeenLastCalledWith(500);
  fireEvent.change(field, { target: { value: '7' } });
  fireEvent.keyDown(field, { key: 'Enter' });
  expect(onCharLimit).toHaveBeenLastCalledWith(40);
});
