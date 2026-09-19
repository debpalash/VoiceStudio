// Audiobook Expressive Maturity — frontend surface (#1208).
//
// Covers the four things a WebUI regression would silently break: the markup
// reference lists the reaction tags; the overrides panel renders + labels its
// controls and persists edits; the request-body mapping only emits touched
// values (so an untouched panel stays byte-identical) and carries the language
// + emotion fields; and the emotion block is engine-conditional (no dead
// controls). Logic-level like ssmlLite.test.js, plus a light RTL render.
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

import en from '../i18n/locales/en.json';
import { DEFAULT_OVERRIDES } from '../store/longformSlice';
import AudiobookOverrides, { overridesToRequest } from '../components/audiobook/AudiobookOverrides';

const withI18n = (node) => <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;

describe('audiobook markup reference lists the reaction tags (#1208 D1)', () => {
  it('markup_hint advertises the VoiceStudio reaction tags', () => {
    const hint = en.audiobook.markup_hint;
    for (const tag of ['[laughter]', '[sigh]', '[question-en]', '[dissatisfaction-hnn]']) {
      expect(hint).toContain(tag);
    }
  });
});

describe('overridesToRequest — only touched values reach the wire (#1208 D2/D3/D5)', () => {
  it('an untouched panel + Auto language emits an empty body (today, byte-identical)', () => {
    expect(overridesToRequest(DEFAULT_OVERRIDES, 'Auto')).toEqual({});
  });

  it('seamless-join gaps ride along only once touched, and 0 / off are real values', () => {
    expect(
      overridesToRequest({ ...DEFAULT_OVERRIDES, lineGapMs: 0, trimEdges: false }, 'Auto'),
    ).toEqual({
      line_gap_ms: 0,
      trim_edges: false,
    });
    expect(overridesToRequest({ ...DEFAULT_OVERRIDES, paragraphGapMs: 900 }, 'Auto')).toEqual({
      paragraph_gap_ms: 900,
    });
  });

  it('language is sent when a non-Auto pick is made (fixes the D5 omission)', () => {
    expect(overridesToRequest(DEFAULT_OVERRIDES, 'Spanish')).toEqual({ language: 'Spanish' });
    expect(overridesToRequest(DEFAULT_OVERRIDES, 'Auto').language).toBeUndefined();
  });

  it('maps sampling knobs to snake_case, omitting nulls', () => {
    const body = overridesToRequest(
      {
        ...DEFAULT_OVERRIDES,
        numStep: 40,
        guidanceScale: 3,
        posTemp: 1.5,
        classTemp: 0.4,
        postprocess: false,
        seed: 7,
      },
      'Auto',
    );
    expect(body).toEqual({
      num_step: 40,
      guidance_scale: 3,
      position_temperature: 1.5,
      class_temperature: 0.4,
      postprocess_output: false,
      seed: 7,
    });
  });

  it('vary_repeats is only present when on', () => {
    expect(overridesToRequest({ ...DEFAULT_OVERRIDES, varyRepeats: true }, 'Auto')).toEqual({
      vary_repeats: true,
    });
    expect('vary_repeats' in overridesToRequest(DEFAULT_OVERRIDES, 'Auto')).toBe(false);
  });

  it('emotion text + alpha map through; empty emotion text drops both', () => {
    expect(
      overridesToRequest({ ...DEFAULT_OVERRIDES, emoText: 'sad', emoAlpha: 0.5 }, 'Auto'),
    ).toEqual({ emo_text: 'sad', emo_alpha: 0.5 });
    // Alpha alone (no description) sends nothing — the description is the anchor.
    expect(overridesToRequest({ ...DEFAULT_OVERRIDES, emoAlpha: 0.5 }, 'Auto')).toEqual({});
  });
});

describe('AudiobookOverrides panel — renders, labels, persists (#1208 D2)', () => {
  const open = (props = {}) => {
    const onChange = vi.fn();
    render(
      withI18n(
        <AudiobookOverrides
          t={i18n.t.bind(i18n)}
          overrides={DEFAULT_OVERRIDES}
          onChange={onChange}
          {...props}
        />,
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: new RegExp(en.audiobook.expressive, 'i') }));
    return onChange;
  };

  it('exposes the labelled sampling controls and persists an edit', () => {
    const onChange = open();
    // Labelled sliders (reused clone.* labels) are present.
    expect(screen.getByLabelText(en.clone.steps)).toBeTruthy();
    expect(screen.getByLabelText(en.clone.pos_temp)).toBeTruthy();
    const steps = screen.getByLabelText(en.clone.steps);
    fireEvent.change(steps, { target: { value: '48' } });
    expect(onChange).toHaveBeenCalledWith({ numStep: 48 });
  });

  it('persists the cache opt-out toggle', () => {
    const onChange = open();
    fireEvent.click(screen.getByLabelText(en.audiobook.vary_repeats, { exact: false }));
    expect(onChange).toHaveBeenCalledWith({ varyRepeats: true });
  });

  it('hides emotion controls unless the active engine supports them', () => {
    open({ emotionSupported: false });
    expect(screen.queryByLabelText(en.audiobook.emotion_text)).toBeNull();
  });

  it('shows emotion controls when the engine supports them', () => {
    const onChange = open({ emotionSupported: true });
    const emo = screen.getByLabelText(en.audiobook.emotion_text);
    expect(emo).toBeTruthy();
    fireEvent.change(emo, { target: { value: 'weary' } });
    expect(onChange).toHaveBeenCalledWith({ emoText: 'weary' });
  });
});
