import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const items = [
  {
    id: 'p1',
    type: 'preset',
    name: 'Shared Designer',
    _source_repo: 'repo/test',
    use_case: 'narration',
    language: 'English',
    instruct: 'female, high pitch',
    attrs: { Gender: 'female', Pitch: 'high pitch' },
    facets: { gender: 'female', age: null, pitch: 'high pitch', whisper: false },
  },
  {
    id: 'v1',
    type: 'voice',
    name: 'Shared Recording',
    _source_repo: 'repo/test',
    use_case: 'narration',
    language: 'English',
    facets: { gender: null, age: null, pitch: null, whisper: false },
  },
];

vi.mock('../../api/hooks', () => ({
  useCommunityItems: () => ({ data: { items }, isLoading: false }),
}));
vi.mock('../../api/community', () => ({ communitySubmitUrl: vi.fn() }));
vi.mock('../../api/external', () => ({ openExternal: vi.fn() }));

import CommunityZone from './CommunityZone';

const t = (_key, options = {}) => options.defaultValue || _key;

describe('CommunityZone persona actions', () => {
  it('wires both item types while offering the wand only for designed presets', () => {
    const onPreview = vi.fn();
    const onUse = vi.fn();
    const onDesign = vi.fn();
    const toggleFavorite = vi.fn();
    render(
      <CommunityZone
        t={t}
        playingId={null}
        loadingPreviewId={null}
        favorites={[]}
        toggleFavorite={toggleFavorite}
        onPreview={onPreview}
        onDesign={onDesign}
        onUse={onUse}
        onUseInStories={vi.fn()}
        onUseAsAudiobookDefault={vi.fn()}
        materializingId={null}
        flash={vi.fn()}
      />,
    );

    const previews = screen.getAllByRole('button', { name: 'Preview' });
    const uses = screen.getAllByRole('button', { name: 'Use voice' });
    fireEvent.click(previews[0]);
    fireEvent.click(previews[1]);
    fireEvent.click(uses[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Open in Designer' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Favorite' })[0]);

    expect(onPreview).toHaveBeenNthCalledWith(1, items[0]);
    expect(onPreview).toHaveBeenNthCalledWith(2, items[1]);
    expect(onUse).toHaveBeenCalledWith(items[0]);
    expect(onDesign).toHaveBeenCalledWith(items[0]);
    expect(toggleFavorite).toHaveBeenCalledWith('community:repo/test:p1');
    expect(screen.getAllByRole('button', { name: 'Open in Designer' })).toHaveLength(1);
  });
});
