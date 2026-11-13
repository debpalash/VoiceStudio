import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
  update: vi.fn(),
  error: vi.fn(),
}));

vi.mock('@/lib/api/profiles', () => ({ updateProfileImage: mock.update }));
vi.mock('sonner', () => ({ toast: { error: mock.error } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('./portrait-search', () => ({
  PortraitSearch: ({ disabled, onSelect }: { disabled: boolean; onSelect: (file: File) => void }) => (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onSelect(new File(['search'], 'search.jpg', { type: 'image/jpeg' }))}
    >
      choose searched portrait
    </button>
  ),
}));

import { ProfileImageEditor } from './profile-image-editor';
import type { Profile } from '@/lib/api/types';
import { queryKeys } from '@/lib/query';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('uses the same image update path for upload and searched portraits', async () => {
  const profile = { id: 'voice/1', name: 'Scarlett', image_url: null } as Profile;
  let resolveUpload!: (profile: Profile) => void;
  const uploadResponse = new Promise<Profile>((resolve) => {
    resolveUpload = resolve;
  });
  mock.update.mockImplementation((_id: string, file: File) =>
    Promise.resolve({ ...profile, image_url: `/portrait/${file.name}` }),
  );
  mock.update.mockImplementationOnce(() => uploadResponse);
  const client = new QueryClient();
  client.setQueryData(queryKeys.profiles, [profile]);
  const { container } = render(
    <QueryClientProvider client={client}>
      <ProfileImageEditor profile={profile} />
    </QueryClientProvider>,
  );

  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  const upload = new File(['upload'], 'upload.png', { type: 'image/png' });
  fireEvent.change(input, { target: { files: [upload] } });
  await waitFor(() => expect(mock.update).toHaveBeenCalledWith('voice/1', upload));
  const search = screen.getByRole('button', { name: 'choose searched portrait' });
  expect(input).toBeDisabled();
  expect(search).toBeDisabled();
  fireEvent.click(search);
  expect(mock.update).toHaveBeenCalledTimes(1);
  expect(client.getQueryData<Profile[]>(queryKeys.profiles)?.[0].image_url).toBeNull();

  // Invocation is not completion: wait for the upload to commit and release
  // the busy state before exercising the second image source.
  await act(async () => {
    resolveUpload({ ...profile, image_url: '/portrait/upload.png' });
  });
  await waitFor(() => expect(search).toBeEnabled());
  expect(client.getQueryData<Profile[]>(queryKeys.profiles)?.[0].image_url).toBe(
    '/portrait/upload.png',
  );
  fireEvent.click(search);
  await waitFor(() => expect(mock.update).toHaveBeenCalledTimes(2));
  expect(mock.update.mock.calls[1][1].name).toBe('search.jpg');
  await waitFor(() =>
    expect(client.getQueryData<Profile[]>(queryKeys.profiles)?.[0].image_url).toBe(
      '/portrait/search.jpg',
    ),
  );
});
