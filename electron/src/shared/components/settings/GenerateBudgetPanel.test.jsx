import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// `toast(...)` (the generic/warning call the shadowed-save path uses) is a
// callable, not just an object with .success/.error — mirror
// generatePreflight.test.js's shape so both call forms work under mock.
const { toastFn } = vi.hoisted(() => {
  const fn = vi.fn();
  fn.success = vi.fn();
  fn.error = vi.fn();
  return { toastFn: fn };
});
vi.mock('react-hot-toast', () => ({ default: toastFn, toast: toastFn }));

vi.mock('../../api/client', () => ({
  apiJson: vi.fn(),
  apiPost: vi.fn(),
}));

import { toast } from 'react-hot-toast';
import { apiJson, apiPost } from '../../api/client';
import GenerateBudgetPanel from './GenerateBudgetPanel';

describe('GenerateBudgetPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiJson.mockResolvedValue({
      generate_timeout_s: 300,
      cpu_generate_timeout_s: 600,
      generate_timeout_shadowed: false,
      cpu_generate_timeout_shadowed: false,
    });
    apiPost.mockResolvedValue({ key: 'x', set: true, shadowed: false });
  });

  it('prefills both budgets from /system/info', async () => {
    render(<GenerateBudgetPanel />);
    await waitFor(() =>
      expect(screen.getByTestId('generate-timeout-input-OMNIVOICE_GENERATE_TIMEOUT_S')).toHaveValue(
        300,
      ),
    );
    expect(
      screen.getByTestId('generate-timeout-input-OMNIVOICE_CPU_GENERATE_TIMEOUT_S'),
    ).toHaveValue(600);
  });

  it('surfaces both env var names', async () => {
    render(<GenerateBudgetPanel />);
    await waitFor(() => screen.getByText('OMNIVOICE_GENERATE_TIMEOUT_S'));
    expect(screen.getByText('OMNIVOICE_CPU_GENERATE_TIMEOUT_S')).toBeInTheDocument();
  });

  it('every row carries the restart-required badge — the value is import-time captured', async () => {
    render(<GenerateBudgetPanel />);
    await waitFor(() => screen.getAllByText('Restart required'));
    expect(screen.getAllByText('Restart required')).toHaveLength(2);
  });

  it('saving posts the new value to /system/set-env', async () => {
    render(<GenerateBudgetPanel />);
    const input = await screen.findByTestId('generate-timeout-input-OMNIVOICE_GENERATE_TIMEOUT_S');
    fireEvent.change(input, { target: { value: '900' } });
    fireEvent.click(screen.getByTestId('generate-timeout-save-OMNIVOICE_GENERATE_TIMEOUT_S'));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith('/system/set-env', {
        key: 'OMNIVOICE_GENERATE_TIMEOUT_S',
        value: '900',
      }),
    );
    expect(toast.success).toHaveBeenCalled();
  });

  it('rejects a non-positive value client-side without calling the API', async () => {
    render(<GenerateBudgetPanel />);
    const input = await screen.findByTestId(
      'generate-timeout-input-OMNIVOICE_CPU_GENERATE_TIMEOUT_S',
    );
    fireEvent.change(input, { target: { value: '0' } });
    fireEvent.click(screen.getByTestId('generate-timeout-save-OMNIVOICE_CPU_GENERATE_TIMEOUT_S'));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(apiPost).not.toHaveBeenCalled();
  });

  it('rejects a value above the max client-side without calling the API', async () => {
    render(<GenerateBudgetPanel />);
    const input = await screen.findByTestId(
      'generate-timeout-input-OMNIVOICE_CPU_GENERATE_TIMEOUT_S',
    );
    fireEvent.change(input, { target: { value: '999999' } });
    fireEvent.click(screen.getByTestId('generate-timeout-save-OMNIVOICE_CPU_GENERATE_TIMEOUT_S'));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(apiPost).not.toHaveBeenCalled();
  });
});

// Review fix (#1787, CodeRabbit/Greptile P1): the panel must never report a
// value as saved-and-applying when an external env var is shadowing it.
describe('GenerateBudgetPanel — external override honesty', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiPost.mockResolvedValue({ key: 'x', set: true, shadowed: false });
  });

  it('shows an override badge instead of "Restart required" for a shadowed row', async () => {
    apiJson.mockResolvedValue({
      generate_timeout_s: 300,
      cpu_generate_timeout_s: 600,
      generate_timeout_shadowed: true,
      cpu_generate_timeout_shadowed: false,
    });
    render(<GenerateBudgetPanel />);

    await waitFor(() =>
      expect(
        screen.getByTestId('generate-timeout-shadowed-OMNIVOICE_GENERATE_TIMEOUT_S'),
      ).toBeInTheDocument(),
    );
    expect(
      screen.queryByTestId('generate-timeout-shadowed-OMNIVOICE_CPU_GENERATE_TIMEOUT_S'),
    ).not.toBeInTheDocument();
    // Only the non-shadowed row still carries the ordinary restart badge.
    expect(screen.getAllByText('Restart required')).toHaveLength(1);
  });

  it('a save into a shadowed row reports the override instead of a plain success toast', async () => {
    apiJson.mockResolvedValue({
      generate_timeout_s: 300,
      cpu_generate_timeout_s: 600,
      generate_timeout_shadowed: false,
      cpu_generate_timeout_shadowed: false,
    });
    apiPost.mockResolvedValue({ key: 'OMNIVOICE_GENERATE_TIMEOUT_S', set: true, shadowed: true });

    render(<GenerateBudgetPanel />);
    const input = await screen.findByTestId('generate-timeout-input-OMNIVOICE_GENERATE_TIMEOUT_S');
    fireEvent.change(input, { target: { value: '900' } });
    fireEvent.click(screen.getByTestId('generate-timeout-save-OMNIVOICE_GENERATE_TIMEOUT_S'));

    await waitFor(() => expect(toastFnCalledWithWarning()).toBe(true));
    expect(toast.success).not.toHaveBeenCalled();
    // The row must now reflect the override rather than "Restart required".
    await waitFor(() =>
      expect(
        screen.getByTestId('generate-timeout-shadowed-OMNIVOICE_GENERATE_TIMEOUT_S'),
      ).toBeInTheDocument(),
    );

    function toastFnCalledWithWarning() {
      return toast.mock.calls.some(([, opts]) => opts?.icon === '⚠️');
    }
  });
});
