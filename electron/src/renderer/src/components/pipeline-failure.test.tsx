import i18next from 'i18next';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { publicFailureFromEvent } from '@/lib/api/failure';
import { PipelineFailure } from './pipeline-failure';

const JA_PROXY = 'プロキシがHTTP 504を返しました。proxy_read_timeout を延ばしてください。';
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { status?: number }) =>
      key === 'tts_errors.proxy_gateway_error'
        ? JA_PROXY.replace('504', String(options?.status))
        : key,
  }),
}));

const { navigate, apiJson } = vi.hoisted(() => ({
  navigate: vi.fn(),
  apiJson: vi.fn(() => Promise.resolve({ ram_after: 1, vram_after: 0, unloaded_model: true })),
}));
vi.mock('@/router', () => ({ router: { navigate } }));
vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiJson,
}));
vi.mock('./install-stt-button', () => ({ InstallSttButton: () => <button>install-stt</button> }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("preserves and opens the backend's exact contextual documentation URL", () => {
  const docsUrl =
    'https://github.com/debpalash/VoiceStudio/blob/main/docs/features/diarization.md#troubleshooting';
  const failure = publicFailureFromEvent(
    {
      reason: 'The installed diarisation runtime failed to load',
      docs_topic: 'DIARIZATION_LOAD_FAILED',
      docs_url: docsUrl,
    },
    'Fallback',
  );

  render(<PipelineFailure failure={failure} fallback="Fallback" />);

  expect(screen.getByRole('link', { name: /dub.open_docs/ })).toHaveAttribute('href', docsUrl);
});

it('rejects a non-web documentation URL and uses the shared safe fallback', () => {
  render(
    <PipelineFailure
      failure={{ reason: 'Failure', docsUrl: 'javascript:alert(1)' }}
      fallback="Fallback"
    />,
  );

  expect(screen.getByRole('link', { name: /dub.open_docs/ })).toHaveAttribute(
    'href',
    'https://github.com/debpalash/VoiceStudio/blob/main/docs/install/troubleshooting.md',
  );
});

it.each([
  ['dub_speech_missing', 'dubIntegrity.missingSpeech'],
  ['dub_timing_overflow', 'dubIntegrity.timingOverflow'],
  ['dub_source_changed', 'dubIntegrity.sourceChanged'],
])('localizes %s while retaining diagnostics', (errorCode, key) => {
  const translate = vi.spyOn(i18next, 't').mockReturnValue('Localized recovery instructions');
  try {
    const failure = publicFailureFromEvent(
      { error_code: errorCode, reason: 'Raw engine error', diagnostic: 'segment a' },
      'Fallback',
    );
    expect(failure.reason).toBe('Localized recovery instructions');
    expect(failure.diagnostic).toBe('segment a');
    expect(translate).toHaveBeenCalledWith(key);
  } finally {
    translate.mockRestore();
  }
});

it('shows the localized no-audio message for a silent video and keeps the diagnostic', () => {
  const translate = vi
    .spyOn(i18next, 't')
    .mockImplementation(((key: string) =>
      key === 'tts_errors.no_audio_track' ? 'Localized: no audio track' : key) as never);
  try {
    const diagnostic =
      'FFmpeg exited with code 234: Output file does not contain any stream. Error opening output files: Invalid argument';
    const failure = publicFailureFromEvent(
      {
        type: 'error',
        stage: 'extract',
        reason: 'This file has no audio track, so there is no speech to transcribe, dub or clone.',
        docs_topic: 'NO_AUDIO_TRACK',
        hint: 'English guidance',
        diagnostic,
      },
      'Task failed',
    );
    expect(failure.reason).toBe('Localized: no audio track');
    expect(failure.hint).toBeUndefined();
    expect(failure.diagnostic).toBe(diagnostic);

    render(<PipelineFailure failure={failure} fallback="Task failed" />);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Localized: no audio track');
    expect(alert).not.toHaveTextContent('code 234');
    expect(screen.getByRole('button', { name: /dub.copy_diagnostic/ })).toBeInTheDocument();
  } finally {
    translate.mockRestore();
  }
});

it.each([
  ['GPU_ARCH_UNSUPPORTED', 'tts_errors.gpu_arch_unsupported'],
  ['WINDOWS_APP_CONTROL_BLOCKED', 'tts_errors.windows_app_control_blocked'],
  ['AUDIO_IO_FAILED', 'tts_errors.audio_io_failed'],
])('localizes %s pipeline recovery without duplicating the API fallback', (topic, key) => {
  const translate = vi.spyOn(i18next, 't').mockReturnValue('Localized recovery instructions');
  try {
    const failure = publicFailureFromEvent(
      {
        docs_topic: topic,
        reason: 'API fallback',
        hint: 'English guidance',
        error_class: 'RuntimeError',
      },
      'Fallback',
    );
    expect(failure.reason).toBe('Localized recovery instructions');
    expect(failure.hint).toBeUndefined();
    expect(failure.errorClass).toBe('RuntimeError');
    expect(failure.docsTopic).toBe(topic);
    expect(translate).toHaveBeenCalledWith(key);
  } finally {
    translate.mockRestore();
  }
});

it.each([
  ['CUDA out of memory. Tried to allocate 2.00 GiB', 'vram'],
  ['Model is not downloaded yet', 'model_missing'],
  ['A generation is already running', 'busy'],
  ['Request timed out after 300s', 'timeout'],
])('turns "%s" into a %s cause with fix chips and the raw text under Details', (raw, cause) => {
  render(<PipelineFailure fallback={raw} />);
  expect(screen.getByText(`failureCause.${cause}.title`)).toBeInTheDocument();
  expect(screen.getByText(`failureCause.${cause}.fix1`)).toBeInTheDocument();
  expect(screen.getByText('failureCause.details')).toBeInTheDocument();
  expect(screen.getByText(raw)).toBeInTheDocument();
});

it('never renders an HTML gateway page as the message', () => {
  const html = '<html><head><title>504 Gateway Time-out</title></head><body>nginx</body></html>';
  render(<PipelineFailure fallback={html} />);
  expect(screen.getByText('failureCause.timeout.title')).toBeInTheDocument();
  expect(screen.queryByText(html)).not.toBeInTheDocument();
  expect(document.body.innerHTML).not.toContain('<title>');
});

it('keeps a failure that already carries curated guidance', () => {
  render(
    <PipelineFailure
      failure={{ reason: 'GPU ran out of memory, close apps', hint: 'Use CPU' }}
      fallback="x"
    />,
  );
  expect(screen.getByText('GPU ran out of memory, close apps')).toBeInTheDocument();
  expect(screen.queryByText('failureCause.details')).not.toBeInTheDocument();
});

it('keeps our own translated proxy advice instead of the generic timeout cause', () => {
  render(<PipelineFailure fallback={JA_PROXY.replace('504', '504')} />);
  expect(screen.getByText(JA_PROXY)).toBeInTheDocument();
  expect(screen.queryByText('failureCause.timeout.title')).not.toBeInTheDocument();
});

describe('diagnosed failure classes', () => {
  const RAW_STT =
    'Automatic reference transcription needs an installed speech-to-text model. Provide a matching reference transcript, or install and select a speech-to-text model in Model Catalogue, then try again.';

  it('localizes backend English text it recognises and offers the install fix', () => {
    render(<PipelineFailure fallback={RAW_STT} />);
    const alert = screen.getByRole('alert');
    expect(alert.querySelector('p')).toHaveTextContent('tts_errors.reference_stt_missing');
    expect(screen.getByText('install-stt')).toBeInTheDocument();
    // The English sentence stays reachable, never as the headline.
    expect(screen.getByText('failureCause.details')).toBeInTheDocument();
  });

  it('uses the topic the caller holds and opens the matching settings page', async () => {
    render(<PipelineFailure fallback="Couldn't write the file." topic="DISK_FULL" />);
    expect(screen.getByText('tts_errors.disk_full')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /failureAction.storage/ }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/settings/storage' }));
  });

  it('frees memory in place for an out-of-memory failure', async () => {
    render(<PipelineFailure failure={{ reason: 'x', docsTopic: 'GPU_OOM' }} fallback="x" />);
    fireEvent.click(screen.getByRole('button', { name: /failureAction.free_memory/ }));
    await waitFor(() =>
      expect(apiJson).toHaveBeenCalledWith('/system/flush-memory?unload_model=true', {
        method: 'POST',
      }),
    );
  });

  it('shows no action for a class the app cannot fix itself', () => {
    render(
      <PipelineFailure failure={{ reason: 'x', docsTopic: 'VIDEO_DRM_PROTECTED' }} fallback="x" />,
    );
    expect(screen.getByText('failureClass.video_drm_protected')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /failureAction/ })).not.toBeInTheDocument();
  });
});
