/**
 * The update announcement must be a toast with actions — not a wall of text,
 * and not a 6-pixel dot.
 *
 * Release notes for a version are the whole changelog section: v0.4.1's was 42
 * bullets. Older builds put that in a blocking OS dialog that filled the screen
 * and had to be dismissed before the app could be used at all. Removing the
 * dialog left the opposite failure — the only remaining signal was a dot beside
 * the version number in the footer, which is easy to never notice.
 *
 * What is pinned here: the toast says which version, offers install and a route
 * to the notes, never renders the notes inline, and cannot stack duplicates
 * when the 6-hourly re-check fires again.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

// vi.hoisted: vi.mock factories are lifted above every import, so anything
// they close over has to be hoisted with them rather than declared below.
const { toastCalls, dismissed, installUpdate, openSettingsTab, storeState } = vi.hoisted(() => ({
  toastCalls: [],
  dismissed: [],
  installUpdate: vi.fn(),
  openSettingsTab: vi.fn(),
  storeState: { dubStep: 'idle', stage: 'idle', ttsInflight: 0 },
}));

vi.mock('react-hot-toast', () => {
  const toast = vi.fn();
  toast.custom = vi.fn((render, opts) => {
    toastCalls.push({ render, opts });
    return opts?.id;
  });
  toast.dismiss = vi.fn((id) => dismissed.push(id));
  return { default: toast, toast };
});

vi.mock('../utils/updater', () => ({ installUpdate: (...a) => installUpdate(...a) }));

vi.mock('../store', () => ({
  useAppStore: Object.assign(() => undefined, {
    getState: () => ({ openSettingsTab, ...storeState }),
  }),
}));

import UpdateToastBody, { showUpdateToast } from '../components/UpdateToast';

const withI18n = (node) => <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;

beforeEach(() => {
  toastCalls.length = 0;
  dismissed.length = 0;
  installUpdate.mockClear();
  openSettingsTab.mockClear();
  Object.assign(storeState, { dubStep: 'idle', stage: 'idle', ttsInflight: 0 });
});

afterEach(() => vi.clearAllMocks());

describe('showUpdateToast', () => {
  it('announces the version', async () => {
    showUpdateToast('0.4.2');
    expect(toastCalls).toHaveLength(1);

    render(withI18n(toastCalls[0].render({ id: 'x' })));
    expect(await screen.findByText(/0\.4\.2 is available/i)).toBeTruthy();
  });

  it('uses one id per version, so a re-check cannot stack duplicates', () => {
    showUpdateToast('0.4.2');
    showUpdateToast('0.4.2');
    // Same id both times — react-hot-toast replaces rather than appends.
    expect(toastCalls.map((c) => c.opts.id)).toEqual([
      'update-available-0.4.2',
      'update-available-0.4.2',
    ]);
  });

  it('never auto-dismisses — an unanswered update is still true', () => {
    showUpdateToast('0.4.2');
    expect(toastCalls[0].opts.duration).toBe(Infinity);
  });

  it('does nothing without a version', () => {
    showUpdateToast(null);
    expect(toastCalls).toHaveLength(0);
  });
});

describe('the toast body', () => {
  it('offers install, notes and dismiss', async () => {
    render(withI18n(<UpdateToastBody id="t1" version="0.4.2" />));
    expect(await screen.findByText(/Install and restart/i)).toBeTruthy();
    expect(screen.getByText(/What's new/i)).toBeTruthy();
    expect(screen.getByText(/Later/i)).toBeTruthy();
  });

  it('routes to the notes instead of rendering them', async () => {
    render(withI18n(<UpdateToastBody id="t1" version="0.4.2" />));
    fireEvent.click(await screen.findByText(/What's new/i));

    expect(openSettingsTab).toHaveBeenCalledWith('updates');
    expect(dismissed).toContain('t1');
  });

  it('installs on request', async () => {
    render(withI18n(<UpdateToastBody id="t1" version="0.4.2" />));
    fireEvent.click(await screen.findByText(/Install and restart/i));

    expect(installUpdate).toHaveBeenCalled();
    expect(dismissed).toContain('t1');
  });

  // Installing relaunches the process. The guard used to be
  // `dubStep === 'generating'` alone, which let the relaunch through — and
  // silently discarded the work — during every other long operation.
  it.each([
    ['a dub upload', { dubStep: 'uploading' }],
    ['a dub transcription', { dubStep: 'transcribing' }],
    ['a dub synth', { dubStep: 'generating' }],
    ['a dub being stopped', { dubStep: 'stopping' }],
    ['a translation', { stage: 'translating' }],
    ['an ASR model load', { stage: 'loading-model' }],
    ['a dictation capture', { stage: 'recording' }],
    ['an export', { stage: 'exporting' }],
    ['a standalone TTS synth', { ttsInflight: 1 }],
    ['a voice preview overlapping another synth', { ttsInflight: 2 }],
  ])('refuses to restart during %s', async (_label, state) => {
    Object.assign(storeState, state);
    render(withI18n(<UpdateToastBody id="t1" version="0.4.2" />));
    fireEvent.click(await screen.findByText(/Install and restart/i));

    expect(installUpdate).not.toHaveBeenCalled();
  });

  // The mirror image: a guard that never lets go is just a broken updater.
  it.each([
    ['nothing is running', { dubStep: 'idle', stage: 'idle', ttsInflight: 0 }],
    ['the transcript is open for editing', { dubStep: 'editing' }],
    ['a dub has finished', { dubStep: 'done' }],
  ])('still installs when %s', async (_label, state) => {
    Object.assign(storeState, state);
    render(withI18n(<UpdateToastBody id="t1" version="0.4.2" />));
    fireEvent.click(await screen.findByText(/Install and restart/i));

    expect(installUpdate).toHaveBeenCalled();
  });

  it('Later removes it without installing', async () => {
    render(withI18n(<UpdateToastBody id="t1" version="0.4.2" />));
    fireEvent.click(await screen.findByText(/Later/i));

    expect(dismissed).toContain('t1');
    expect(installUpdate).not.toHaveBeenCalled();
  });

  it('renders no release notes inline, however long they are', () => {
    const { container } = render(withI18n(<UpdateToastBody id="t1" version="0.4.2" />));
    // The 42-bullet changelog is what made the old dialog unusable. The toast
    // takes no notes prop at all — this asserts the shape stays that way.
    expect(container.textContent).not.toMatch(/Highlights|### |^- /m);
    expect(container.textContent.length).toBeLessThan(200);
  });
});
