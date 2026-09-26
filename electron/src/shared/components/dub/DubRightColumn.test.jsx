import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../DubSegmentTable', () => ({ default: () => null }));

import DubRightColumn from './DubRightColumn';

const noop = () => {};

function column(multiBatchBusy, setDubLang = noop, setDubLangCode = noop, overrides = {}) {
  return (
    <DubRightColumn
      t={(key) => key}
      preserveBg={false}
      setPreserveBg={noop}
      dualSubs={false}
      setDualSubs={noop}
      burnSubs={false}
      setBurnSubs={noop}
      defaultTrack="original"
      setDefaultTrack={noop}
      dubLangCode="bn"
      multiLangMode
      batchTargets={[
        { lang: 'Bengali', code: 'bn' },
        { lang: 'Spanish', code: 'es' },
      ]}
      multiBatchBusy={multiBatchBusy}
      setDubLang={setDubLang}
      setDubLangCode={setDubLangCode}
      dubTracks={[]}
      timingStrategy="concise"
      setTimingStrategy={noop}
      voiceMatch="per_line"
      setVoiceMatch={noop}
      dubTranscript=""
      showTranscript={false}
      setShowTranscript={noop}
      dubJobId={null}
      glossaryVisible={false}
      selectedSegIds={new Set()}
      speakerClones={{}}
      profiles={[]}
      showCheckpoint={false}
      isTranslating={false}
      dubSegments={[]}
      dubStep="editing"
      {...overrides}
    />
  );
}

describe('DubRightColumn language targets', () => {
  it('keeps transcript and glossary disclosure actions reachable beside the editor', async () => {
    const setShowTranscript = vi.fn();
    const setGlossaryOpen = vi.fn();
    const setGlossaryHidden = vi.fn();
    await act(async () => {
      render(
        column(false, noop, noop, {
          dubJobId: 'project',
          dubTranscript: 'Original dialogue',
          setShowTranscript,
          setGlossaryOpen,
          setGlossaryHidden,
        }),
      );
    });
    fireEvent.click(screen.getByRole('button', { name: 'dub.transcript' }));
    expect(setShowTranscript).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByRole('button', { name: /glossary.title/ }));
    expect(setGlossaryOpen).toHaveBeenCalledWith(true);
    expect(setGlossaryHidden).toHaveBeenCalledWith(false);
  });
  it('keeps output controls compact while exposing current choices and editable settings', async () => {
    const setTimingStrategy = vi.fn();
    let rerender;
    await act(async () => {
      ({ rerender } = render(column(false, noop, noop, { preserveBg: true, setTimingStrategy })));
    });
    const toggle = screen.getByRole('button', { name: 'dub.output_options' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveTextContent('dub.timing_concise');
    expect(toggle).toHaveTextContent('dub.voice_match_per_line');
    expect(toggle).toHaveTextContent('dub.mix_bg_audio');
    expect(screen.queryByRole('radio', { name: 'dub.timing_smart_fit' })).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(screen.getByRole('radio', { name: 'dub.timing_smart_fit' }));
    expect(setTimingStrategy).toHaveBeenCalledWith('smart_fit');
    rerender(column(false, noop, noop, { timingStrategy: 'smart_fit', setTimingStrategy }));
    fireEvent.click(toggle);
    expect(toggle).toHaveTextContent('dub.timing_smart_fit');
    expect(toggle).not.toHaveTextContent('dub.mix_bg_audio');
    fireEvent.click(toggle);
    expect(screen.getByRole('radio', { name: 'dub.timing_smart_fit' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });
  it('applies bulk voice and language choices through searchable menus, including resets', async () => {
    const apply = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    await act(async () => {
      render(
        column(false, noop, noop, {
          selectedSegIds: new Set(['one', 'two']),
          bulkApplyToSelected: apply,
          profiles: [
            { id: 'clone-1', name: 'Aria' },
            { id: 'design-1', name: 'Narrator', instruct: 'calm' },
          ],
          speakerClones: { 'Speaker 1': {} },
        }),
      );
    });
    const voice = () => screen.getByRole('button', { name: 'dub.set_voice' });
    fireEvent.click(voice());
    fireEvent.change(screen.getByRole('listbox').querySelector('input'), {
      target: { value: 'Aria' },
    });
    fireEvent.keyDown(screen.getByRole('listbox').querySelector('input'), { key: 'Enter' });
    expect(apply).toHaveBeenLastCalledWith({ profile_id: 'clone-1' });
    expect(voice()).toHaveTextContent('dub.set_voice');
    fireEvent.click(voice());
    fireEvent.mouseDown(screen.getByRole('option', { name: 'Speaker 1' }));
    expect(apply).toHaveBeenLastCalledWith({ profile_id: 'auto:speaker_1' });
    fireEvent.click(voice());
    fireEvent.mouseDown(screen.getByRole('option', { name: 'Narrator' }));
    expect(apply).toHaveBeenLastCalledWith({ profile_id: 'design-1' });
    fireEvent.click(voice());
    fireEvent.mouseDown(screen.getByRole('option', { name: 'dub.clear_voice' }));
    expect(apply).toHaveBeenLastCalledWith({ profile_id: '' });
    const language = () => screen.getByRole('button', { name: 'dub.set_lang' });
    fireEvent.click(language());
    fireEvent.mouseDown(
      within(screen.getByRole('listbox')).getByRole('option', { name: /Bengali/ }),
    );
    expect(apply).toHaveBeenLastCalledWith({ target_lang: 'bn' });
    fireEvent.click(language());
    fireEvent.mouseDown(screen.getByRole('option', { name: 'dub.default_lang' }));
    expect(apply).toHaveBeenLastCalledWith({ target_lang: null });
  });
  it('localizes the lip-sync timing option', async () => {
    await act(async () => {
      render(column(false));
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole('button', { name: 'dub.output_options' }));
    expect(screen.getByRole('radio', { name: 'dub.timing_lip_sync' })).toBeInTheDocument();
  });

  it('disables language switches for the full shared batch lock', async () => {
    const setDubLang = vi.fn();
    const setDubLangCode = vi.fn();
    let rerender;
    await act(async () => {
      ({ rerender } = render(column(true, setDubLang, setDubLangCode)));
      await Promise.resolve();
    });
    const language = screen.getByRole('button', { name: 'dub.language' });

    expect(language).toBeDisabled();
    expect(setDubLangCode).not.toHaveBeenCalled();

    await act(async () => {
      rerender(column(false, setDubLang, setDubLangCode));
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole('button', { name: 'dub.language' }));
    fireEvent.mouseDown(screen.getByRole('option', { name: /Spanish/ }));
    expect(setDubLang).toHaveBeenCalledWith('Spanish');
    expect(setDubLangCode).toHaveBeenCalledWith('es');
  });

  it('renders the first available dub when the saved default is stale', () => {
    render(
      column(false, noop, noop, {
        defaultTrack: 'fr',
        dubLangCode: 'bn',
        dubTracks: ['es'],
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'dub.output_options' }));
    expect(screen.getByRole('button', { name: 'dub.default_track' })).toHaveTextContent(
      'dub.dub_track',
    );
  });
});
