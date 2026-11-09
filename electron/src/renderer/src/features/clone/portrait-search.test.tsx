import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ search: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.search }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { PortraitSearch } from './portrait-search';

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it('keeps existing results disabled until the pending portrait save finishes', async () => {
  mock.search.mockResolvedValue({ images: [{ title: 'Portrait result', data: 'aW1hZ2U=' }] });
  const onSelect = vi.fn();
  const { rerender } = render(<PortraitSearch name="Scarlett" onSelect={onSelect} />);
  fireEvent.click(screen.getByRole('button', { name: 'profileIdentity.search' }));
  const result = await screen.findByRole('button', { name: 'Portrait result' });

  rerender(<PortraitSearch name="Scarlett" disabled onSelect={onSelect} />);
  expect(result).toBeDisabled();
  fireEvent.click(result);
  expect(onSelect).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Portrait result' })).toBe(result);

  rerender(<PortraitSearch name="Scarlett" onSelect={onSelect} />);
  expect(result).toBeEnabled();
  fireEvent.click(result);
  expect(onSelect).toHaveBeenCalledTimes(1);
  expect(onSelect.mock.calls[0][0]).toBeInstanceOf(File);
  expect(screen.queryByRole('button', { name: 'Portrait result' })).toBeNull();
});
