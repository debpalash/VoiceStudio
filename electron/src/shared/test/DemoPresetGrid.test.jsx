import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

import DemoPresetGrid from '../components/DemoPresetGrid';

const PRESETS = [
  {
    id: 'p1',
    name: 'The Librarian',
    icon: '📚',
    description: 'Warm UK narrator',
    instruct: 'female, middle-aged, low pitch, british accent',
    attrs: { Gender: 'female', Age: 'middle-aged' },
    script: 'Once upon a time…',
    preview_url: '/demo_audio/voice_design/p1.wav',
    language: 'English',
  },
  {
    id: 'p2',
    name: 'The Anchor',
    icon: '📺',
    description: 'US news broadcaster',
    instruct: 'male, middle-aged, moderate pitch, american accent',
    attrs: { Gender: 'male' },
    script: 'Good evening…',
    preview_url: '/demo_audio/voice_design/p2.wav',
    language: 'English',
  },
];

function withI18n(node) {
  return <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;
}

describe('DemoPresetGrid', () => {
  it('renders one card per preset', () => {
    render(withI18n(<DemoPresetGrid presets={PRESETS} onUse={vi.fn()} />));
    expect(screen.getByText('The Librarian')).toBeInTheDocument();
    expect(screen.getByText('The Anchor')).toBeInTheDocument();
    expect(screen.getAllByText('Use this design →')).toHaveLength(2);
  });

  it('shows the instruct taxonomy string on each card', () => {
    render(withI18n(<DemoPresetGrid presets={PRESETS} onUse={vi.fn()} />));
    expect(screen.getByText(/british accent/)).toBeInTheDocument();
    expect(screen.getByText(/american accent/)).toBeInTheDocument();
  });

  it('calls onUse with the preset object when "Use this design" is clicked', () => {
    const onUse = vi.fn();
    render(withI18n(<DemoPresetGrid presets={PRESETS} onUse={onUse} />));
    const buttons = screen.getAllByText('Use this design →');
    fireEvent.click(buttons[0]);
    expect(onUse).toHaveBeenCalledWith(PRESETS[0]);
  });

  // Regression for #316 — single-playback invariant. jsdom doesn't implement
  // HTMLMediaElement playback, so stub play/pause and assert coordination.
  it('starting a second preview stops the first (single playback, #316)', async () => {
    const play = vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const pause = vi.spyOn(window.HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    try {
      render(withI18n(<DemoPresetGrid presets={PRESETS} onUse={vi.fn()} />));

      fireEvent.click(screen.getByLabelText('Preview The Librarian'));
      expect(await screen.findByLabelText('Pause The Librarian')).toBeInTheDocument();

      fireEvent.click(screen.getByLabelText('Preview The Anchor'));
      // Claiming the playback manager for card 2 pauses card 1's audio.
      expect(pause).toHaveBeenCalled();
      expect(await screen.findByLabelText('Pause The Anchor')).toBeInTheDocument();
      // Card 1 is back to its idle Preview state — only one plays at a time.
      expect(screen.getByLabelText('Preview The Librarian')).toBeInTheDocument();
      expect(play).toHaveBeenCalledTimes(2);
    } finally {
      play.mockRestore();
      pause.mockRestore();
    }
  });

  it('clicking a playing preview stops it (#316)', async () => {
    const play = vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const pause = vi.spyOn(window.HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    try {
      render(withI18n(<DemoPresetGrid presets={PRESETS} onUse={vi.fn()} />));

      fireEvent.click(screen.getByLabelText('Preview The Librarian'));
      fireEvent.click(await screen.findByLabelText('Pause The Librarian'));

      expect(pause).toHaveBeenCalled();
      expect(await screen.findByLabelText('Preview The Librarian')).toBeInTheDocument();
    } finally {
      play.mockRestore();
      pause.mockRestore();
    }
  });
});
