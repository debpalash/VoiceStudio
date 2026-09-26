import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../WaveformPlayer', () => ({
  default: ({ src, source }) => (
    <div data-testid={`waveform-${source}`}>{typeof src === 'string' ? src : src.name}</div>
  ),
}));

// The shared voice picker pulls react-query + archetypes — reduce it to a
// plain <select> so the panel's own wiring is what's under test.
vi.mock('../VoiceSelector', () => ({
  default: ({ value, onChange, profiles }) => (
    <select aria-label="voice-selector" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">—</option>
      {profiles.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </select>
  ),
}));

const recordingState = vi.hoisted(() => ({
  isRecording: false,
  isStartingRecording: false,
  isCleaning: false,
  recordingTime: 0,
  startRecording: vi.fn(),
  stopRecording: vi.fn(),
}));

// Mic capture needs real getUserMedia — inert here.
vi.mock('../../hooks/useRecording', () => ({
  default: () => recordingState,
}));

const convertSpeech = vi.fn();
vi.mock('../../api/convert', () => ({
  convertSpeech: (...args) => convertSpeech(...args),
}));

const toastAsrModelMissing = vi.fn();
vi.mock('../../utils/asrModelMissing', () => ({
  asrMissingPayload: (err) => (err?.detail?.error === 'asr_model_missing' ? err.detail : null),
  toastAsrModelMissing: (...args) => toastAsrModelMissing(...args),
}));

const toastModelNotDownloaded = vi.fn();
vi.mock('../../utils/modelNotDownloaded', () => ({
  modelNotDownloadedPayload: (err) =>
    err?.detail?.error === 'model_not_downloaded' ? err.detail : null,
  toastModelNotDownloaded: (...args) => toastModelNotDownloaded(...args),
}));

const toastErrorWithReport = vi.fn();
vi.mock('../../utils/errorToast', () => ({
  toastErrorWithReport: (...args) => toastErrorWithReport(...args),
}));

// The panel only needs the busy-error class from the generate API surface —
// mock the module so the test doesn't drag the real client/store chain in.
vi.mock('../../api/generate', () => ({
  TtsGenerationBusyError: class TtsGenerationBusyError extends Error {},
}));

const toastError = vi.fn();
const toastPlain = vi.fn();
vi.mock('react-hot-toast', () => {
  const toast = Object.assign((...args) => toastPlain(...args), {
    error: (...args) => toastError(...args),
  });
  return { toast };
});

import ConvertMethodPanel from './ConvertMethodPanel';
import { TtsGenerationBusyError } from '../../api/generate';

const t = (key) => key;
const profiles = [{ id: 'vp-1', name: 'Morgan' }];

function addSourceClip() {
  const file = new File(['wav-bytes'], 'source.wav', { type: 'audio/wav' });
  file.arrayBuffer = async () => new ArrayBuffer(8);
  fireEvent.change(document.getElementById('convert-audio-upload'), {
    target: { files: [file] },
  });
  return file;
}

beforeEach(() => {
  recordingState.isStartingRecording = false;
  recordingState.isCleaning = false;
  convertSpeech.mockReset();
  toastAsrModelMissing.mockReset();
  toastModelNotDownloaded.mockReset();
  toastErrorWithReport.mockReset();
  toastError.mockReset();
  toastPlain.mockReset();
});

describe('ConvertMethodPanel', () => {
  it('keeps the primary action outside the scrolling form', () => {
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    const action = screen.getByTestId('convert-action-bar');
    expect(action).toHaveClass('studio-action-bar');
    expect(screen.getByTestId('convert-form')).not.toContainElement(action);
    expect(action).toContainElement(screen.getByRole('button', { name: 'convert.convert' }));
  });
  it('shows microphone startup instead of a duplicate record action', () => {
    recordingState.isStartingRecording = true;
    const onRecordingBusyChange = vi.fn();
    const { unmount } = render(
      <ConvertMethodPanel
        t={t}
        profiles={profiles}
        onRecordingBusyChange={onRecordingBusyChange}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Starting…');
    expect(screen.queryByRole('button', { name: 'Record' })).not.toBeInTheDocument();
    expect(onRecordingBusyChange).toHaveBeenLastCalledWith(true);
    unmount();
    expect(onRecordingBusyChange).toHaveBeenLastCalledWith(false);
  });

  it('keeps method switching busy through recording cleanup', () => {
    const onRecordingBusyChange = vi.fn();
    recordingState.isCleaning = true;
    const { rerender } = render(
      <ConvertMethodPanel
        t={t}
        profiles={profiles}
        onRecordingBusyChange={onRecordingBusyChange}
      />,
    );
    expect(onRecordingBusyChange).toHaveBeenLastCalledWith(true);
    recordingState.isCleaning = false;
    rerender(
      <ConvertMethodPanel
        t={t}
        profiles={profiles}
        onRecordingBusyChange={onRecordingBusyChange}
      />,
    );
    expect(onRecordingBusyChange).toHaveBeenLastCalledWith(false);
  });
  it.each(['picker', 'drop'])('reports unsupported audio from %s', (source) => {
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    const file = new File(['text'], 'notes.txt', { type: 'text/plain' });
    const input = document.getElementById('convert-audio-upload');
    if (source === 'picker') fireEvent.change(input, { target: { files: [file] } });
    else
      fireEvent.drop(document.querySelector('label[for="convert-audio-upload"]'), {
        dataTransfer: { files: [file] },
      });
    expect(toastError).toHaveBeenCalledWith('clone.unsupported_audio');
    expect(screen.queryByTestId('waveform-convert-source')).not.toBeInTheDocument();
  });
  it('keeps Convert disabled until a source clip AND a target voice are set', () => {
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    const button = screen.getByRole('button', { name: 'convert.convert' });
    expect(button).toBeDisabled();
    expect(screen.getByText('convert.need_source_and_voice')).toBeInTheDocument();

    addSourceClip();
    expect(button).toBeDisabled(); // still no voice

    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
    expect(button).toBeEnabled();
    expect(screen.queryByText('convert.need_source_and_voice')).toBeNull();
  });

  it.each(['success', 'failure'])(
    'keeps method switching busy until conversion %s settles',
    async (outcome) => {
      let resolveConversion, rejectConversion;
      convertSpeech.mockImplementation(
        () =>
          new Promise((resolve, reject) => {
            resolveConversion = resolve;
            rejectConversion = reject;
          }),
      );
      const onRecordingBusyChange = vi.fn();
      render(
        <ConvertMethodPanel
          t={t}
          profiles={profiles}
          onRecordingBusyChange={onRecordingBusyChange}
        />,
      );
      addSourceClip();
      fireEvent.change(screen.getByLabelText('voice-selector'), {
        target: { value: 'vp-1' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
      await waitFor(() => expect(convertSpeech).toHaveBeenCalled());
      expect(onRecordingBusyChange).toHaveBeenLastCalledWith(true);
      if (outcome === 'success') resolveConversion({ id: 'take', audio_url: '/audio/take.wav' });
      else rejectConversion(new Error('Conversion failed'));
      await waitFor(() => expect(onRecordingBusyChange).toHaveBeenLastCalledWith(false));
    },
  );

  it('previews the source clip with the shared waveform player', () => {
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    addSourceClip();
    expect(screen.getByTestId('waveform-convert-source')).toHaveTextContent('source.wav');
  });

  it('POSTs audio + profile_id + match_duration (default on) and shows the take', async () => {
    convertSpeech.mockResolvedValue({
      id: 'take0001',
      audio_url: '/audio/take0001.wav',
      text: 'hello there',
      duration_s: 1.4,
    });
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    addSourceClip();
    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));

    await waitFor(() => expect(screen.getByTestId('convert-result')).toBeInTheDocument());

    const fd = convertSpeech.mock.calls[0][0];
    expect(fd.get('profile_id')).toBe('vp-1');
    expect(fd.get('match_duration')).toBe('1');
    expect(fd.get('audio')).toBeInstanceOf(Blob);

    expect(screen.getByTestId('waveform-output').textContent).toContain('/audio/take0001.wav');
    expect(screen.getByText(/hello there/)).toBeInTheDocument();
  });

  it('sends match_duration=0 when the toggle is off', async () => {
    convertSpeech.mockResolvedValue({
      id: 't2',
      audio_url: '/audio/t2.wav',
      text: 'x',
      duration_s: 1,
    });
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    addSourceClip();
    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));

    await waitFor(() => expect(convertSpeech).toHaveBeenCalled());
    expect(convertSpeech.mock.calls[0][0].get('match_duration')).toBe('0');
  });

  it('invalidates a pending conversion when duration matching changes', async () => {
    convertSpeech.mockImplementation(
      (_fd, { signal } = {}) =>
        new Promise((_, reject) => {
          signal?.addEventListener('abort', () =>
            reject(Object.assign(new Error('aborted'), { name: 'AbortError' })),
          );
        }),
    );
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    addSourceClip();
    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
    await waitFor(() => expect(convertSpeech).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('checkbox'));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'convert.convert' })).toBeEnabled(),
    );
    expect(convertSpeech.mock.calls[0][1].signal.aborted).toBe(true);
    expect(screen.queryByTestId('convert-result')).toBeNull();
    expect(toastErrorWithReport).not.toHaveBeenCalled();
  });

  it('clears a completed take when duration matching changes', async () => {
    convertSpeech.mockResolvedValue({
      id: 'duration-take',
      audio_url: '/audio/duration-take.wav',
      text: 'done',
      duration_s: 1,
    });
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    addSourceClip();
    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
    await waitFor(() => expect(screen.getByTestId('convert-result')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('checkbox'));
    expect(screen.queryByTestId('convert-result')).toBeNull();
  });

  it('routes the typed asr_model_missing 409 to the download CTA toast', async () => {
    const err = new Error('409');
    err.detail = { error: 'asr_model_missing', recommended: { repo_id: 'org/whisper' } };
    convertSpeech.mockRejectedValue(err);
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    addSourceClip();
    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));

    await waitFor(() => expect(toastAsrModelMissing).toHaveBeenCalledWith(err.detail));
    expect(toastErrorWithReport).not.toHaveBeenCalled();
    expect(screen.queryByTestId('convert-result')).toBeNull();
  });

  it('routes other backend errors through the shared actionable toast', async () => {
    // toastErrorWithReport owns the mapping: [shutting_down]/[clone_ref_*]
    // markers become localized guidance, everything else gets the "Report"
    // action — never a raw technical string via a bare toast.error.
    const err = new Error('503 Service Unavailable: [shutting_down]');
    convertSpeech.mockRejectedValue(err);
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    addSourceClip();
    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));

    await waitFor(() =>
      expect(toastErrorWithReport).toHaveBeenCalledWith('tts_errors.error_prefix', err),
    );
    expect(toastError).not.toHaveBeenCalled();
  });

  it('shows the localized busy notice when another generation holds admission', async () => {
    convertSpeech.mockRejectedValue(new TtsGenerationBusyError('busy'));
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    addSourceClip();
    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));

    await waitFor(() =>
      expect(toastPlain).toHaveBeenCalledWith(
        'tts_errors.generation_in_progress',
        expect.anything(),
      ),
    );
    expect(toastErrorWithReport).not.toHaveBeenCalled();
  });

  it('ignores a response that lands after the source clip changed', async () => {
    let resolveConvert;
    convertSpeech.mockImplementation(() => new Promise((resolve) => (resolveConvert = resolve)));
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    addSourceClip();
    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
    await waitFor(() => expect(convertSpeech).toHaveBeenCalled());

    // The user swaps the source clip while the request is still in flight…
    addSourceClip();
    resolveConvert({ id: 'stale', audio_url: '/audio/stale.wav', text: 'old', duration_s: 1 });

    // …so the obsolete take must never render against the new inputs.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'convert.convert' })).toBeEnabled(),
    );
    expect(screen.queryByTestId('convert-result')).toBeNull();
  });

  it('aborts the in-flight request on input change and frees Convert immediately', async () => {
    // Faithful to the real client: the promise only settles when the abort
    // signal fires — a change of inputs must not wait out the old request.
    convertSpeech.mockImplementation(
      (fd, { signal } = {}) =>
        new Promise((_, reject) => {
          signal?.addEventListener('abort', () =>
            reject(Object.assign(new Error('aborted'), { name: 'AbortError' })),
          );
        }),
    );
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    addSourceClip();
    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
    await waitFor(() => expect(convertSpeech).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: 'convert.converting' })).toBeDisabled();

    // Swapping the source aborts the obsolete request → button usable again
    // without waiting for the old response, and the abort stays silent.
    addSourceClip();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'convert.convert' })).toBeEnabled(),
    );
    expect(convertSpeech.mock.calls[0][1].signal.aborted).toBe(true);
    expect(toastErrorWithReport).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
    expect(screen.queryByTestId('convert-result')).toBeNull();
  });

  it('releases Convert while an obsolete source preflight is still pending', async () => {
    let releaseOldBuffer;
    let bufferCalls = 0;
    const arrayBuffer = vi.spyOn(File.prototype, 'arrayBuffer').mockImplementation(() => {
      bufferCalls += 1;
      if (bufferCalls === 1) {
        return new Promise((resolve) => {
          releaseOldBuffer = () => resolve(new ArrayBuffer(8));
        });
      }
      return Promise.resolve(new ArrayBuffer(8));
    });
    let resolveCurrent;
    convertSpeech.mockImplementation(() => new Promise((resolve) => (resolveCurrent = resolve)));

    try {
      render(<ConvertMethodPanel t={t} profiles={profiles} />);
      addSourceClip();
      fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
      fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
      await waitFor(() => expect(bufferCalls).toBe(1));

      // This invalidates the request before it reaches fetch. The replacement
      // input must be usable without waiting for the old File read to settle.
      addSourceClip();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'convert.convert' })).toBeEnabled(),
      );
      fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
      await waitFor(() => expect(convertSpeech).toHaveBeenCalledTimes(1));
      expect(screen.getByRole('button', { name: 'convert.converting' })).toBeDisabled();

      // The obsolete preflight may finish now, but its finally block must not
      // release the newer request's busy state or dispatch the old source.
      releaseOldBuffer();
      await waitFor(() => expect(bufferCalls).toBe(2));
      expect(convertSpeech).toHaveBeenCalledTimes(1);
      expect(screen.getByRole('button', { name: 'convert.converting' })).toBeDisabled();

      resolveCurrent({ id: 'fresh', audio_url: '/audio/fresh.wav', text: 'new', duration_s: 1 });
      await waitFor(() => expect(screen.getByTestId('convert-result')).toBeInTheDocument());
    } finally {
      arrayBuffer.mockRestore();
    }
  });

  it('clears a completed take when the target voice changes', async () => {
    convertSpeech.mockResolvedValue({
      id: 't3',
      audio_url: '/audio/t3.wav',
      text: 'done',
      duration_s: 1,
    });
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    addSourceClip();
    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
    await waitFor(() => expect(screen.getByTestId('convert-result')).toBeInTheDocument());

    // Picking a different voice must drop the old take — that audio belongs
    // to the previous selection.
    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: '' } });
    expect(screen.queryByTestId('convert-result')).toBeNull();
  });

  it('ignores an error that lands after the target voice changed', async () => {
    let rejectConvert;
    convertSpeech.mockImplementation(() => new Promise((_, reject) => (rejectConvert = reject)));
    render(<ConvertMethodPanel t={t} profiles={profiles} />);
    addSourceClip();
    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: 'vp-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'convert.convert' }));
    await waitFor(() => expect(convertSpeech).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText('voice-selector'), { target: { value: '' } });
    rejectConvert(new Error('too late'));

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'convert.convert' })).toBeDisabled(),
    );
    expect(toastErrorWithReport).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
  });
});
