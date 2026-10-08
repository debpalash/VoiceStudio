import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ generate: vi.fn(), warning: vi.fn(), success: vi.fn() }));
vi.mock('sonner', () => ({ toast: { warning: mocks.warning, success: mocks.success } }));
vi.mock('@/lib/api/generate', () => ({ generateClone: mocks.generate }));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { to: string }) => (
    <a href={to} {...props}>{children}</a>
  ),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/waveform-player', () => ({
  WaveformPlayer: () => <div data-testid="preview-player" />,
}));
import { ProfilePreview } from './profile-preview';
import { acquireSynthesis } from '@/lib/synthesis-lock';
import { cloneSettingsStore } from '@/lib/store/clone-settings';
import type { Profile } from '@/lib/api/types';
const profile = { id: 'voice', language: 'French', instruct: 'female', seed: 1234 } as Profile;
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
function mount() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ProfilePreview profile={profile} />
    </QueryClientProvider>,
  );
}
it('previews the saved profile without modifying the script settings', async () => {
  const before = cloneSettingsStore.state;
  mocks.generate.mockResolvedValue({ blob: new Blob(['audio']) });
  mount();
  fireEvent.click(screen.getByText('voice_profile.try_voice'));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Bonjour' } });
  fireEvent.click(screen.getByRole('button', { name: 'voice_profile.gen_preview' }));
  await screen.findByTestId('preview-player');
  expect(mocks.generate).toHaveBeenCalledWith(
    expect.objectContaining({
      text: 'Bonjour',
      profileId: 'voice',
      language: 'French',
      seed: 1234,
    }),
    expect.anything(),
  );
  expect(cloneSettingsStore.state).toBe(before);
});
it('refuses a competing interactive synthesis', async () => {
  const release = acquireSynthesis()!;
  try {
    mount();
    fireEvent.click(screen.getByText('voice_profile.try_voice'));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Hello' } });
    fireEvent.click(screen.getByRole('button', { name: 'voice_profile.gen_preview' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('tts_errors.generation_in_progress');
    expect(mocks.generate).not.toHaveBeenCalled();
  } finally {
    release();
  }
});
it('aborts a closed pane and releases its synthesis slot without late playback', async () => {
  let finish!: (result: { blob: Blob }) => void;
  mocks.generate.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const view = mount();
  fireEvent.click(screen.getByText('voice_profile.try_voice'));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Hello' } });
  fireEvent.click(screen.getByRole('button', { name: 'voice_profile.gen_preview' }));
  view.unmount();
  expect(mocks.generate.mock.calls[0][1].signal.aborted).toBe(true);
  finish({ blob: new Blob() });
  await waitFor(() => {
    const release = acquireSynthesis();
    expect(release).not.toBeNull();
    release?.();
  });
});

it('discloses speech the backend dropped from a profile preview', async () => {
  mocks.generate.mockResolvedValue({ blob: new Blob(['audio']), dropped: { count: 1, text: '' } });
  mount();
  fireEvent.click(screen.getByText('voice_profile.try_voice'));
  fireEvent.click(screen.getByRole('button', { name: 'voice_profile.gen_preview' }));
  await screen.findByTestId('preview-player');
  expect(mocks.warning).toHaveBeenCalledWith('tts.droppedChunks', expect.anything());
});
