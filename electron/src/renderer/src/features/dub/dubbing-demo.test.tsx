import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  videoProps: [] as Array<{ controls?: string; source: string }>,
  players: new Map<
    string,
    {
      currentTime: number;
      paused: boolean;
      play: ReturnType<typeof vi.fn>;
      pause: ReturnType<typeof vi.fn>;
    }
  >(),
}));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/api/client', () => ({ apiJson: mocks.api, apiPath: (path: string) => path }));
vi.mock('@/components/video-player', () => ({
  VideoPlayer: ({ controls, playerRef, onPlay, source }: Record<string, any>) => {
    mocks.videoProps.push({ controls, source });
    let player = mocks.players.get(source);
    if (!player) {
      player = {
        currentTime: 0,
        paused: true,
        play: vi.fn(async () => {
          player!.paused = false;
        }),
        pause: vi.fn(async () => {
          player!.paused = true;
        }),
      };
      mocks.players.set(source, player);
    }
    playerRef.current = player;
    return (
      <button
        type="button"
        aria-label={source}
        onClick={() => {
          player!.paused = false;
          onPlay?.({});
        }}
      />
    );
  },
}));

import { DubbingDemo } from './dubbing-demo';

const manifest = {
  source: {
    code: 'en',
    label: 'English',
    video: 'source.mp4',
    script: 'Original script',
  },
  dubbed: [
    {
      code: 'es',
      label: 'Español',
      video: 'dubbed_es.mp4',
      script: 'Spanish script',
      dir: 'ltr' as const,
    },
    {
      code: 'fr',
      label: 'Français',
      video: 'dubbed_fr.mp4',
      script: 'French script',
      dir: 'ltr' as const,
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mocks.players.clear();
  mocks.videoProps.length = 0;
});

function mount(onEdit = vi.fn()) {
  mocks.api.mockResolvedValue(manifest);
  render(<DubbingDemo onDismiss={vi.fn()} onTry={vi.fn()} onEdit={onEdit} />);
}

it('keeps A/B playback exclusive while synchronizing the peer playhead', async () => {
  mount();
  const sourceButton = await screen.findByRole('button', {
    name: 'dubbing-demo-comparison-demo.original_tag',
  });
  const source = mocks.players.get('dubbing-demo-comparison-demo.original_tag')!;
  const dubbed = mocks.players.get('dubbing-demo-comparison-demo.dubbed_tag')!;
  expect(mocks.videoProps.slice(0, 2).map(({ controls }) => controls)).toEqual([
    'compact',
    'compact',
  ]);
  source.currentTime = 4.25;

  fireEvent.click(sourceButton);

  expect(dubbed.currentTime).toBe(4.25);
  expect(dubbed.play).not.toHaveBeenCalled();
});

it('keeps editable transcript drafts for each sample language', async () => {
  mount();
  const transcripts = await screen.findAllByRole('textbox');
  expect(transcripts).toHaveLength(2);
  fireEvent.change(transcripts[1]!, { target: { value: 'Edited Spanish script' } });

  fireEvent.click(screen.getByRole('button', { name: 'Français' }));
  expect(screen.getAllByRole('textbox')[1]).toHaveValue('French script');
  fireEvent.change(screen.getAllByRole('textbox')[1]!, {
    target: { value: 'Edited French script' },
  });

  fireEvent.click(screen.getByRole('button', { name: 'Español' }));
  expect(screen.getAllByRole('textbox')[1]).toHaveValue('Edited Spanish script');
});

it('opens the selected dubbed sample in the editor', async () => {
  const onEdit = vi.fn();
  mount(onEdit);

  fireEvent.click(await screen.findByRole('button', { name: 'clone.edit Español' }));

  expect(onEdit).toHaveBeenCalledWith({
    path: '/demo_audio/demo/dubbing/dubbed_es.mp4',
    filename: 'dubbed_es.mp4',
  });
});
