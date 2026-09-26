import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  saveVoiceAsProfile: vi.fn(),
  voice: { id: 'import-1', name: 'Imported Voice', duration: 4 },
}));

vi.mock('../../api/hooks', () => ({
  useGalleryVoices: () => ({
    data: [mocks.voice],
    isLoading: false,
    refetch: vi.fn(),
  }),
}));
vi.mock('../../api/gallery', () => ({
  deleteGalleryVoice: vi.fn(),
  downloadYoutubeClip: vi.fn(),
  previewVoiceUrl: vi.fn(),
  saveVoiceAsProfile: mocks.saveVoiceAsProfile,
  searchYoutube: vi.fn(),
  uploadVoiceClip: vi.fn(),
}));
vi.mock('../../api/profiles', () => ({ importPersona: vi.fn() }));
vi.mock('../../api/client', () => ({ apiFetch: vi.fn() }));
vi.mock('../../utils/dialog', () => ({ askConfirm: vi.fn() }));
vi.mock('../AudioTrimmer', () => ({ default: () => null }));

import ImportsZone from './ImportsZone';

const t = (_key, options = {}) => options.defaultValue || _key;

describe('ImportsZone Use voice', () => {
  beforeEach(() => vi.clearAllMocks());

  it('materializes once on a double click and hands off the returned profile', async () => {
    let finish;
    mocks.saveVoiceAsProfile.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const onUseProfile = vi.fn();
    render(
      <ImportsZone
        t={t}
        playingId={null}
        loadingPreviewId={null}
        onPlayGallery={vi.fn()}
        onUseProfile={onUseProfile}
        flash={vi.fn()}
      />,
    );

    const useButton = screen.getByRole('button', { name: 'Use voice' });
    fireEvent.click(useButton);
    fireEvent.click(useButton);
    expect(mocks.saveVoiceAsProfile).toHaveBeenCalledOnce();
    expect(useButton).toBeDisabled();

    const profile = { profile_id: 'profile-1', name: 'Server Voice' };
    finish(profile);
    await waitFor(() => expect(onUseProfile).toHaveBeenCalledWith(profile, 'studio'));
    expect(useButton).not.toBeDisabled();
  });

  it('can send an imported voice directly to Stories', async () => {
    const profile = { profile_id: 'profile-2', name: 'Story Voice' };
    mocks.saveVoiceAsProfile.mockResolvedValue(profile);
    const onUseProfile = vi.fn();
    render(
      <ImportsZone
        t={t}
        playingId={null}
        loadingPreviewId={null}
        onPlayGallery={vi.fn()}
        onUseProfile={onUseProfile}
        flash={vi.fn()}
      />,
    );

    fireEvent.pointerDown(screen.getByRole('button', { name: 'More actions' }), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Use in Stories' }));

    await waitFor(() => expect(onUseProfile).toHaveBeenCalledWith(profile, 'stories'));
  });
});
