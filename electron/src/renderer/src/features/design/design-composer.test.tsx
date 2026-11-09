import type { ReactNode } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import '@/i18n';
import type { UseGenerateClone } from '@/hooks/use-generate';
import { DesignComposer } from './design-composer';

const readiness = vi.hoisted(() => ({ blocker: null as 'engine' | null }));

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: ReactNode }) => <a>{children}</a>,
}));
vi.mock('@tanstack/react-query', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-query')>()),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock('@/hooks/use-tts-readiness', () => ({ useTtsReadiness: () => readiness.blocker }));
vi.mock('@/hooks/use-model-install', () => ({
  useModelInstall: () => ({ view: null, busy: false, start: vi.fn() }),
}));
vi.mock('@/features/clone/engine-language-picker', () => ({
  EngineLanguagePicker: () => <button type="button">English</button>,
}));
vi.mock('@/features/clone/quality-controls', () => ({
  QualityControls: () => <button type="button">Standard</button>,
}));
vi.mock('@/features/clone/quality-offer', () => ({ QualityOffer: () => null }));
vi.mock('@/features/clone/action-bar', () => ({
  VoiceControls: () => <button type="button">Voice controls</button>,
}));

const synthesize = vi.fn();

function generation(overrides: Partial<UseGenerateClone> = {}): UseGenerateClone {
  return {
    isGenerating: false,
    elapsedSeconds: 0,
    progress: null,
    stage: 'preparing',
    modelStage: null,
    modelProgress: null,
    error: null,
    errorTopic: null,
    cancel: vi.fn(),
    clearError: vi.fn(),
    ...overrides,
  } as UseGenerateClone;
}

function renderComposer(blocker: 'engine' | null, overrides: Partial<UseGenerateClone> = {}) {
  readiness.blocker = blocker;
  return render(
    <DesignComposer
      generation={generation(overrides)}
      blocker={blocker}
      ready
      engineName="Test engine"
      onSynthesize={synthesize}
    />,
  );
}

beforeEach(() => synthesize.mockClear());

it('wraps the action below the tools instead of overlapping them in a narrow column', () => {
  renderComposer(null);
  const bar = screen.getByTestId('design-composer');
  // A fixed-width grid let the language and quality pills slide under the
  // Synthesize button (#composer overlap); the bar must wrap instead.
  expect(bar).toHaveClass('flex', 'flex-wrap');
  expect(bar.className).not.toMatch(/grid-cols/);
  const action = screen.getByRole('button', { name: 'Synthesize audio' });
  expect(action.className).not.toMatch(/\bw-(52|64)\b/);
  // No hidden cancel button reserving width while idle.
  expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument();
});

it('synthesizes from the primary action when ready', () => {
  renderComposer(null);
  fireEvent.click(screen.getByRole('button', { name: 'Synthesize audio' }));
  expect(synthesize).toHaveBeenCalledTimes(1);
});

it('turns the primary action into setup while text-to-speech is not ready', () => {
  renderComposer('engine');
  expect(screen.queryByRole('button', { name: 'Synthesize audio' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Set up' })).toBeEnabled();
  expect(screen.getByRole('status')).toHaveTextContent('Text-to-speech is not ready');
  // One primary action: no second Fix button in a banner.
  expect(screen.queryByRole('button', { name: 'Fix' })).not.toBeInTheDocument();
});

it('shows cancel and elapsed time inside the bar while rendering', () => {
  renderComposer(null, { isGenerating: true, elapsedSeconds: 2.5, stage: 'generating' });
  expect(screen.getByRole('button', { name: 'Cancel generation' })).toBeEnabled();
  expect(screen.getByText('2.5s')).toBeInTheDocument();
});
