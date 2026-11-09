import type { ReactNode } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import '@/i18n';
import type { ExpressionCaps } from '@/lib/api/types';
import { EmotionControls } from './emotion-controls';
import {
  cloneSettingsStore,
  DEFAULT_CLONE_SETTINGS,
  patchCloneSettings,
} from '@/lib/store/clone-settings';

const engine = vi.hoisted(() => ({
  supportsEmotion: true,
  name: 'IndexTTS 2.5 (multilingual emotion-controlled cloning)',
  caps: undefined as ExpressionCaps | undefined,
}));
vi.mock('@/hooks/use-engines', () => ({
  useEngines: () => ({
    activeTts: {
      id: 'indextts2',
      display_name: engine.name,
      supports_emotion: engine.supportsEmotion,
      expression_caps: engine.caps,
    },
  }),
}));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="#models">{children}</a>,
}));

beforeEach(() => {
  cloneSettingsStore.setState(() => ({ ...DEFAULT_CLONE_SETTINGS }));
  engine.supportsEmotion = true;
  engine.name = 'IndexTTS 2.5 (multilingual emotion-controlled cloning)';
  engine.caps = undefined;
});

describe('engine-aware emotion entry (#3422)', () => {
  it('stays visible and explains why for engines without emotion', async () => {
    engine.supportsEmotion = false;
    engine.name = 'VoiceStudio (k2-fsa/OmniVoice, 600+ languages)';
    engine.caps = { emotion: 'none', tags: ['[laughter]', '[sigh]'] };
    render(<EmotionControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(
      await screen.findByText(/Emotion isn't available with OmniVoice\. IndexTTS2/),
    ).toBeInTheDocument();
    expect(screen.getByText(/reactions such as \[laughter\] from Insert/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sliders' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reset' })).not.toBeInTheDocument();
  });

  it('takes a written delivery direction where instruct steers the clone', async () => {
    engine.supportsEmotion = false;
    engine.name = 'CosyVoice 3 (9 langs, zero-shot, instruct, Apache-2.0)';
    engine.caps = { emotion: 'direction', tags: ['[breath]', '[laughter]'] };
    render(<EmotionControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    const field = await screen.findByRole('textbox', { name: 'Delivery direction' });
    fireEvent.change(field, { target: { value: 'scared, then excited' } });
    // Its own draft, not the Style instruct another engine would also read.
    expect(cloneSettingsStore.state.direction).toBe('scared, then excited');
    expect(cloneSettingsStore.state.instruct).toBe('');
    expect(screen.getByText(/guidance for CosyVoice 3\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(cloneSettingsStore.state.direction).toBe('');
  });

  it('keeps the legacy gate for a backend without expression_caps', async () => {
    engine.supportsEmotion = false;
    render(<EmotionControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(await screen.findByText(/Emotion isn't available/)).toBeInTheDocument();
  });
});

describe('IndexTTS2 clone emotion controls', () => {
  it('keeps the default take neutral and accepts a written emotion prompt', async () => {
    render(<EmotionControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(cloneSettingsStore.state.emotionMode).toBe('none');

    fireEvent.click(await screen.findByRole('button', { name: 'Description' }));
    const textarea = screen.getByRole('textbox', { name: 'Emotion description' });
    fireEvent.change(textarea, { target: { value: 'whispering with sadness' } });
    expect(cloneSettingsStore.state.emotionMode).toBe('text');
    expect(cloneSettingsStore.state.emotionText).toBe('whispering with sadness');
    expect(screen.getByText('0.60')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Off' }));
    expect(cloneSettingsStore.state.emotionMode).toBe('none');
    // The text is a draft and is restored if Description is selected again.
    expect(cloneSettingsStore.state.emotionText).toBe('whispering with sadness');
  });

  it('offers eight correctly ordered vector sliders and an isolated reset', async () => {
    patchCloneSettings({
      emotionMode: 'vector',
      emotionVector: [0.4, 0, 0, 0, 0, 0.2, 0, 0],
      speed: 1.5,
    });
    render(<EmotionControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(await screen.findByRole('button', { name: 'Sliders' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    // Base UI mounts slider groups before their measured thumbs in jsdom.
    // Assert the eight real controls plus labels; browser rendering supplies thumbs.
    expect(document.querySelectorAll('[data-slot="slider"]')).toHaveLength(8);
    for (const label of [
      'Happy',
      'Angry',
      'Sad',
      'Afraid',
      'Disgusted',
      'Melancholic',
      'Surprised',
      'Calm',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(cloneSettingsStore.state.emotionMode).toBe('none');
    expect(cloneSettingsStore.state.emotionVector).toEqual([0, 0, 0, 0, 0, 0, 0, 0]);
    expect(cloneSettingsStore.state.speed).toBe(1.5);
  });

  it('disables editing while a take is being synthesized', async () => {
    render(<EmotionControls disabled />);
    fireEvent.click(screen.getByRole('button', { name: 'Emotion' }));
    expect(await screen.findByRole('button', { name: 'Description' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Sliders' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reset' })).toBeDisabled();
  });
});
