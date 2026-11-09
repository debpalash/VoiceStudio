import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AudioFileDetails } from './audio-file-details';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values ? `${key} ${JSON.stringify(values)}` : key,
  }),
}));
vi.mock('@/lib/audio/wav-info', () => ({
  readWavInfo: () => ({ sampleRate: 24000, channels: 1, bits: 32, encoding: 'float' }),
}));

afterEach(cleanup);

it('keeps the full file facts reachable from the keyboard in the compact line', async () => {
  render(<AudioFileDetails blob={new Blob([new Uint8Array(44)])} compact />);
  const trigger = await screen.findByRole('button', { name: /cloneQuality\.fileInfo/ });
  expect(trigger).toHaveTextContent('cloneQuality.sampleRate');
  trigger.focus();
  expect(trigger).toHaveFocus();
});
