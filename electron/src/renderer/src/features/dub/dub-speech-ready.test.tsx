import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { DubSpeechReady } from './dub-speech-ready';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
afterEach(cleanup);

it('offers a visible, named play control for a completed take during generation', () => {
  const play = vi.fn();
  const { rerender } = render(<DubSpeechReady onPlay={play} loading={false} />);
  expect(screen.getByText('dub.speech_ready')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'dub.play_generated_speech' }));
  expect(play).toHaveBeenCalledOnce();
  rerender(<DubSpeechReady onPlay={play} loading />);
  expect(screen.getByRole('button').hasAttribute('disabled')).toBe(true);
});
