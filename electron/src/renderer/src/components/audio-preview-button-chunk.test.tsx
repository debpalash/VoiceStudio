import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { activeAppWorkCount } from '@/lib/app-activity';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('./audio-preview-player', () => {
  throw new Error('chunk');
});
import { AudioPreviewButton } from './audio-preview-button';

afterEach(cleanup);

it('shows retry and releases the activity when the player chunk fails to load', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  render(<AudioPreviewButton src="/a" source="s" activity="synthesis" />);
  fireEvent.click(screen.getByRole('button'));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'player.unavailable · common.retry' })).toBeEnabled(),
  );
  expect(activeAppWorkCount()).toBe(0);
});
