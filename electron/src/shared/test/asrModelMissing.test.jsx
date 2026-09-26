/**
 * Typed "no ASR model installed" error → one-click download CTA.
 *
 * Only the TTS model is required on a fresh install; ASR consumers answer
 * with `{ error: 'asr_model_missing', recommended: {…} }` over three
 * transports (HTTP 409 detail, dub SSE error event, dictation WS frame).
 * Pins the payload normalization + the CTA wiring: the button starts the
 * install via POST /models/install and, for dictation picks, persists
 * dictation.model_id so a retry actually uses the downloaded model.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const toastError = vi.fn();
const toastSuccess = vi.fn();
const toastLoading = vi.fn();
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), {
    error: (...a) => toastError(...a),
    success: (...a) => toastSuccess(...a),
    loading: (...a) => toastLoading(...a),
    dismiss: vi.fn(),
  }),
}));

const installModel = vi.fn();
const listModels = vi.fn();
vi.mock('../api/setup', () => ({
  installModel: (...a) => installModel(...a),
  listModels: (...a) => listModels(...a),
  setupDownloadStreamUrl: () => 'http://localhost/setup/download-stream',
}));

const apiPost = vi.fn();
vi.mock('../api/client', () => ({ apiPost: (...a) => apiPost(...a) }));

vi.mock('i18next', () => ({
  default: { t: (key, opts) => (opts ? `${key} ${JSON.stringify(opts)}` : key) },
}));

import { asrMissingPayload, toastAsrModelMissing } from '../utils/asrModelMissing';

const PAYLOAD = {
  error: 'asr_model_missing',
  missing_repo_id: 'Systran/faster-whisper-large-v3',
  recommended: {
    repo_id: 'Systran/faster-whisper-large-v3',
    label: 'Whisper large-v3',
    size_gb: 2.9,
  },
};

describe('asrMissingPayload', () => {
  it('extracts from a structured ApiError detail (HTTP 409)', () => {
    const err = Object.assign(new Error('409'), { status: 409, detail: PAYLOAD });
    expect(asrMissingPayload(err)).toEqual(PAYLOAD);
  });

  it('extracts from a raw SSE/WS payload', () => {
    expect(asrMissingPayload(PAYLOAD)).toEqual(PAYLOAD);
    expect(
      asrMissingPayload({ type: 'error', kind: 'asr_model_missing', ...PAYLOAD }),
    ).toBeTruthy();
  });

  it('extracts from an Error tagged by the dub SSE handler', () => {
    const err = Object.assign(new Error('detail'), { asrModelMissing: PAYLOAD });
    expect(asrMissingPayload(err)).toEqual(PAYLOAD);
  });

  it('returns null for anything else', () => {
    expect(asrMissingPayload(null)).toBeNull();
    expect(asrMissingPayload(new Error('boom'))).toBeNull();
    expect(asrMissingPayload({ detail: 'plain string 409' })).toBeNull();
    expect(asrMissingPayload({ error: 'other_error' })).toBeNull();
  });
});

describe('toastAsrModelMissing', () => {
  let stream;

  beforeEach(() => {
    toastError.mockReset();
    toastSuccess.mockReset();
    installModel.mockReset();
    listModels.mockReset();
    apiPost.mockReset();
    toastLoading.mockReset();
    listModels.mockResolvedValue({ models: [] });
    globalThis.EventSource = class FakeEventSource {
      constructor(url) {
        this.url = url;
        this.close = vi.fn();
        stream = this;
      }

      emit(event) {
        this.onmessage?.({ data: JSON.stringify(event) });
      }
    };
  });

  function renderToast(payload) {
    toastAsrModelMissing(payload);
    expect(toastError).toHaveBeenCalledTimes(1);
    const [content] = toastError.mock.calls[0];
    // Toast content is a render-prop; mount it like react-hot-toast would.
    render(typeof content === 'function' ? content({ id: 'tst-1' }) : content);
  }

  it('renders the download CTA and starts the install on click', async () => {
    installModel.mockResolvedValue({ status: 'started' });
    renderToast(PAYLOAD);
    const btn = screen.getByRole('button');
    expect(btn.textContent).toContain('asr_missing.download');
    fireEvent.click(btn);
    await waitFor(() =>
      expect(installModel).toHaveBeenCalledWith('Systran/faster-whisper-large-v3'),
    );
    expect(toastSuccess).not.toHaveBeenCalled();
    stream.emit({
      repo_id: 'Systran/faster-whisper-large-v3',
      phase: 'aggregate',
      bytes_done: 50,
      total_bytes: 100,
    });
    expect(toastLoading.mock.calls.at(-1)[0]).toContain('50%');
    stream.emit({ repo_id: 'Systran/faster-whisper-large-v3', phase: 'install_done' });
    // Non-dictation pick: no dictation pref write.
    expect(apiPost).not.toHaveBeenCalled();
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });

  it('also persists dictation.model_id for dictation picks', async () => {
    installModel.mockResolvedValue({ status: 'started' });
    apiPost.mockResolvedValue({});
    renderToast({
      ...PAYLOAD,
      recommended: {
        repo_id: 'csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8',
        label: 'Parakeet TDT v3',
        size_gb: 0.18,
        dictation_id: 'sherpa-parakeet-tdt-v3',
      },
    });
    fireEvent.click(screen.getByRole('button'));
    await waitFor(() => expect(installModel).toHaveBeenCalled());
    stream.emit({
      repo_id: 'csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8',
      phase: 'install_done',
    });
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith('/dictation/prefs', {
        model_id: 'sherpa-parakeet-tdt-v3',
      }),
    );
  });

  it('reports a dictation preference write failure instead of claiming install success', async () => {
    installModel.mockResolvedValue({ status: 'started' });
    apiPost.mockRejectedValue(new Error('prefs unavailable'));
    renderToast({
      ...PAYLOAD,
      recommended: {
        ...PAYLOAD.recommended,
        dictation_id: 'sherpa-parakeet-tdt-v3',
      },
    });
    fireEvent.click(screen.getByRole('button'));
    await waitFor(() => expect(installModel).toHaveBeenCalled());
    stream.emit({
      repo_id: 'Systran/faster-whisper-large-v3',
      phase: 'install_done',
    });

    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(2));
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('degrades to a plain toast when no recommendation resolves', () => {
    toastAsrModelMissing({ error: 'asr_model_missing', recommended: null });
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(typeof toastError.mock.calls[0][0]).toBe('string');
  });
});
