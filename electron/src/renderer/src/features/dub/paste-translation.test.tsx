import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { apiJson } from '@/lib/api/client';
import { PasteTranslation } from './paste-translation';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/api/client', () => ({ apiJson: vi.fn() }));

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it('invalidates the previous preview immediately when timestamped text changes', async () => {
  vi.mocked(apiJson)
    .mockResolvedValueOnce({ segments: [{ start: 0, end: 2, text: 'Old translation' }] })
    .mockResolvedValueOnce({ segments: [{ start: 0, end: 2, text: 'New translation' }] });
  const onApply = vi.fn();
  render(
    <PasteTranslation
      segments={[{ id: 'a', start: 0, end: 2, text: 'Original', text_original: 'Original' }]}
      onApply={onApply}
      onClose={vi.fn()}
    />,
  );

  const input = screen.getByRole('textbox');
  const apply = screen.getByRole<HTMLButtonElement>('button', { name: 'dub.paste_translation_apply' });
  const timestamp = '1\n00:00:00,000 --> 00:00:02,000\n';
  fireEvent.change(input, { target: { value: timestamp + 'Old translation' } });
  await screen.findByText('Old translation');
  expect(apply.disabled).toBe(false);

  fireEvent.change(input, { target: { value: timestamp + 'New translation' } });
  expect(apply.disabled).toBe(true);
  expect(screen.queryByText('Old translation')).toBeNull();
  fireEvent.click(apply);
  expect(onApply).not.toHaveBeenCalled();

  await screen.findByText('New translation');
  await waitFor(() => expect(apply.disabled).toBe(false));
  fireEvent.click(apply);
  expect(onApply).toHaveBeenCalledWith([expect.objectContaining({ after: 'New translation' })]);
});
