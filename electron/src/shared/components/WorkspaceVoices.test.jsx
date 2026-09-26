import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('./WaveformPlayer', () => ({ default: () => null }));

import WorkspaceVoices from './WorkspaceVoices';

const profile = { id: 'voice-1', name: 'Studio voice', instruct: '' };

function renderVoices(overrides = {}) {
  const handleSelectProfile = vi.fn();
  const props = {
    defineMethod: 'audio',
    profiles: [profile],
    selectedProfile: '',
    setSelectedProfile: vi.fn(),
    previewLoading: '',
    handleSelectProfile,
    handleDeleteProfile: vi.fn(),
    handlePreviewVoice: vi.fn(),
    handleUnlockProfile: vi.fn(),
    ...overrides,
  };
  return { ...render(<WorkspaceVoices {...props} />), handleSelectProfile };
}

describe('WorkspaceVoices recording lock', () => {
  it('cannot replace the active reference while microphone startup or capture is active', () => {
    const { handleSelectProfile, rerender } = renderVoices({ selectionDisabled: true });

    fireEvent.click(screen.getByText(profile.name).closest('.history-item'));
    expect(screen.getByRole('button', { name: 'Select' })).toBeDisabled();
    expect(handleSelectProfile).not.toHaveBeenCalled();

    rerender(
      <WorkspaceVoices
        defineMethod="audio"
        profiles={[profile]}
        selectedProfile=""
        setSelectedProfile={vi.fn()}
        previewLoading=""
        handleSelectProfile={handleSelectProfile}
        handleDeleteProfile={vi.fn()}
        handlePreviewVoice={vi.fn()}
        handleUnlockProfile={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText(profile.name).closest('.history-item'));
    expect(handleSelectProfile).toHaveBeenCalledWith(profile);
  });
});
