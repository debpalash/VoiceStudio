import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const hookState = vi.hoisted(() => ({
  model: {
    status: 'idle',
    sub_stage: 'failed',
    error: 'Close other GPU-heavy apps or unload models, then retry.',
  },
  preflight: { checks: [] },
}));

vi.mock('../api/hooks', () => ({
  useModelStatus: () => ({ data: hookState.model, isLoading: false }),
  usePreflight: () => ({ data: hookState.preflight, isLoading: false }),
}));

import ReadinessChecklist from '../components/ReadinessChecklist';

describe('ReadinessChecklist model attribution', () => {
  it('labels /model/status as TTS and never attributes its failure to ASR', () => {
    render(<ReadinessChecklist showWhenAllPass />);
    expect(screen.getByText('TTS Model')).toBeInTheDocument();
    expect(screen.queryByText('ASR Model')).not.toBeInTheDocument();
    expect(screen.getAllByText(/Close other GPU-heavy apps/)).toHaveLength(2);
    expect(screen.queryByText(/transcription/i)).not.toBeInTheDocument();
  });
});
