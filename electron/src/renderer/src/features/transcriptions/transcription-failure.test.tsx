import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

vi.mock('i18next', () => ({ default: { t: (key: string) => `t:${key}` } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

import { ApiError } from '@/lib/api/client';
import { TranscriptionFailure, transcriptionFailureFrom } from './transcription-failure';

afterEach(cleanup);

const GENERIC = 'VoiceStudio hit an internal error; check the backend log for details.';

function show(error: unknown) {
  render(
    <TranscriptionFailure
      failure={transcriptionFailureFrom(error)}
      retryDisabled={false}
      onRetry={() => {}}
      onDismiss={() => {}}
    />,
  );
}

it('shows the remediation of a classified failure instead of only "Transcription failed"', () => {
  // The global 500 body for an OSError(ENOSPC) on POST /transcribe.
  show(
    new ApiError(500, 'disk full localized', {
      detail: `${GENERIC} The disk is full …`,
      docs_topic: 'DISK_FULL',
      hint: 'The disk is full …',
      error_class: 'DISK_FULL',
      error_type: 'OSError',
    }),
  );
  expect(screen.getByRole('alert')).toHaveTextContent('transcriptions.failed');
  expect(screen.getByText('t:tts_errors.disk_full')).toBeVisible();
});

it('localizes every catalogued class and offers its in-app fix', () => {
  show(
    new ApiError(500, GENERIC, {
      detail: GENERIC,
      docs_topic: 'MODEL_CACHE_CORRUPT',
      hint: 'Redownload the model from Model Catalogue.',
    }),
  );
  expect(screen.getByText('t:failureClass.model_cache_corrupt')).toBeVisible();
  expect(screen.queryByText('Redownload the model from Model Catalogue.')).toBeNull();
  expect(screen.getByRole('button', { name: /failureAction.models/ })).toBeInTheDocument();
});

it("falls back to the backend's own hint for a class this build does not know yet", () => {
  show(
    new ApiError(500, GENERIC, {
      detail: GENERIC,
      docs_topic: 'A_FUTURE_CLASS',
      hint: 'Redownload the model from Model Catalogue.',
    }),
  );
  expect(screen.getByText('Redownload the model from Model Catalogue.')).toBeVisible();
});

it('keeps an unclassified failure behind Technical details only', () => {
  show(new ApiError(500, GENERIC, { detail: GENERIC, error_class: 'UNKNOWN' }));
  expect(screen.queryByText(/^t:tts_errors/)).toBeNull();
  expect(screen.getByText(GENERIC).closest('details')).not.toBeNull();
});
