import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Profile } from '@/lib/api/types';

const mock = vi.hoisted(() => ({
  profiles: [] as Profile[],
  select: vi.fn(),
  workspace: vi.fn(),
  update: vi.fn(),
  error: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}));
vi.mock('@/hooks/use-profiles', () => ({
  useProfiles: () => ({ data: mock.profiles, isPending: false }),
}));
vi.mock('@/lib/store/clone-settings', () => ({ useCloneSetting: () => 'b' }));
vi.mock('@/lib/store/reference', () => ({ selectCloneProfile: mock.select }));
vi.mock('@/lib/store/workspace', () => ({ setWorkspace: mock.workspace }));
vi.mock('@/lib/api/profiles', () => ({ updateProfileImage: mock.update }));
vi.mock('sonner', () => ({ toast: { error: mock.error } }));
vi.mock('@tanstack/react-query', async (original) => ({
  ...(await original<typeof import('@tanstack/react-query')>()),
  useQueryClient: () => ({ setQueryData: vi.fn() }),
}));
vi.mock('./reference-panel', () => ({ ReferencePanel: () => <div>reference panel</div> }));

import { VoiceSetup } from './voice-setup';

const voice = (id: string, name: string, created_at: number, language: string | null = null) =>
  ({ id, name, created_at, language, kind: 'clone', ref_audio_path: `${id}.wav` }) as Profile;

beforeEach(() => {
  localStorage.clear();
  mock.profiles = [
    voice('a', 'Zoe', 100),
    voice('b', 'Adam', 300, 'English'),
    voice('c', 'Mia', 200),
    { ...voice('d', 'Design only', 400), kind: 'design' } as Profile,
  ];
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const names = () =>
  screen
    .getAllByRole('button')
    .filter((button) => button.hasAttribute('data-choose'))
    .map((button) => button.getAttribute('aria-label'));

it('lists cloned voices newest first, sorts by name on request and remembers it', () => {
  const view = render(<VoiceSetup onChosen={() => {}} onBack={() => {}} />);
  expect(names()).toEqual(['Adam', 'Mia', 'Zoe']);
  fireEvent.click(screen.getByRole('radio', { name: 'cloneFlow.sort_name' }));
  expect(names()).toEqual(['Adam', 'Mia', 'Zoe']);
  mock.profiles = [voice('x', 'Beta', 1), voice('y', 'Alpha', 2)];
  view.unmount();
  render(<VoiceSetup onChosen={() => {}} onBack={() => {}} />);
  expect(screen.getByRole('radio', { name: 'cloneFlow.sort_name' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  expect(names()).toEqual(['Alpha', 'Beta']);
});

it('marks the current voice, chooses a card and opens editing without choosing', () => {
  const chosen = vi.fn();
  render(<VoiceSetup onChosen={chosen} onBack={() => {}} />);
  const adam = screen.getByRole('button', { name: 'Adam' });
  expect(adam).toHaveAttribute('aria-current', 'true');
  const card = adam.closest('[data-slot="voice-card"]') as HTMLElement;
  expect(within(card).getByText(/English/)).toBeInTheDocument();
  fireEvent.click(within(card).getByRole('button', { name: 'clone.edit_voice: Adam' }));
  expect(mock.workspace).toHaveBeenCalledWith({ editingProfileId: 'b', panel: null });
  expect(chosen).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Mia' }));
  expect(mock.select).toHaveBeenCalledWith(expect.objectContaining({ id: 'c' }));
  expect(chosen).toHaveBeenCalledOnce();
});

it('uploads a photo from the card and rejects unsupported or oversized images', () => {
  mock.update.mockResolvedValue(voice('b', 'Adam', 300));
  render(<VoiceSetup onChosen={() => {}} onBack={() => {}} />);
  const input = screen.getByLabelText('cloneFlow.change_photo: Adam') as HTMLInputElement;
  const gif = new File(['x'], 'a.gif', { type: 'image/gif' });
  fireEvent.change(input, { target: { files: [gif] } });
  expect(mock.error).toHaveBeenCalledWith('profileIdentity.image_limit');
  const huge = new File([new Uint8Array(5 * 1024 * 1024 + 1)], 'a.png', { type: 'image/png' });
  fireEvent.change(input, { target: { files: [huge] } });
  expect(mock.update).not.toHaveBeenCalled();
  const png = new File(['x'], 'a.png', { type: 'image/png' });
  fireEvent.change(input, { target: { files: [png] } });
  expect(mock.update).toHaveBeenCalledWith('b', png);
});

it('searches by name once the list is long enough', () => {
  mock.profiles = ['One', 'Two', 'Three', 'Four', 'Five'].map((name, index) =>
    voice(name, name, index),
  );
  render(<VoiceSetup onChosen={() => {}} onBack={() => {}} />);
  fireEvent.change(screen.getByRole('textbox', { name: 'common.search' }), {
    target: { value: 'tw' },
  });
  expect(names()).toEqual(['Two']);
});
