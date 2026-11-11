import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api, describeError: String }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params ? `${key} ${JSON.stringify(params)}` : key,
  }),
}));

import { GgufSelfTest } from './gguf-self-test';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('runs the GGUF self-test and names the Vulkan GPU, with the log on hover', async () => {
  mock.api.mockResolvedValue({
    ok: true,
    message: 'synthesized',
    duration_ms: 2400,
    sample_rate: 24000,
    audio_seconds: 1.5,
    runtime_device: 'Vulkan 1: AMD Radeon RX 5500M',
    runtime_log: 'ggml_vulkan: 1 = AMD Radeon RX 5500M',
  });
  render(<GgufSelfTest />);
  fireEvent.click(screen.getByRole('button', { name: /engines.selfTest/ }));

  const status = await screen.findByRole('status');
  expect(mock.api).toHaveBeenCalledWith('/engines/omnivoice-gguf/selftest', { method: 'POST' });
  expect(status.textContent).toContain('engines.selfTestOk');
  expect(status.textContent).toContain('Vulkan 1: AMD Radeon RX 5500M');
  expect(status.getAttribute('title')).toBe('synthesized\nggml_vulkan: 1 = AMD Radeon RX 5500M');
  // Keyboard users reach the same detail through a native disclosure.
  const summary = screen.getByText('common.details');
  expect(summary.tagName).toBe('SUMMARY');
  expect(summary.parentElement?.textContent).toContain('ggml_vulkan: 1 = AMD Radeon RX 5500M');
});

it('clears the previous result while a new test runs', async () => {
  mock.api.mockResolvedValueOnce({
    ok: true,
    message: 'synthesized',
    duration_ms: 2400,
    audio_seconds: 1.5,
    runtime_device: 'Vulkan 1: AMD Radeon RX 5500M',
  });
  let finish: (value: unknown) => void = () => {};
  mock.api.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
  render(<GgufSelfTest />);
  fireEvent.click(screen.getByRole('button', { name: /engines.selfTest/ }));
  await screen.findByRole('status');

  fireEvent.click(screen.getByRole('button', { name: /engines.selfTest/ }));
  await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
  expect(screen.queryByText(/RX 5500M/)).toBeNull();
  finish({ ok: false, message: 'boom', duration_ms: 10 });
  expect((await screen.findByRole('status')).textContent).toBe('engines.selfTestFailed');
});

it('reports a timed-out self-test without a device', async () => {
  mock.api.mockResolvedValue({
    ok: false,
    message: 'timed out after 90s',
    duration_ms: 90000,
    timed_out: true,
    runtime_device: null,
  });
  render(<GgufSelfTest />);
  fireEvent.click(screen.getByRole('button', { name: /engines.selfTest/ }));

  const status = await screen.findByRole('status');
  expect(status.textContent).toBe('engines.selfTestTimedOut');
});

it('shows a request failure as visible, actionable text with details', async () => {
  mock.api.mockRejectedValue('backend offline');
  render(<GgufSelfTest />);
  fireEvent.click(screen.getByRole('button', { name: /engines.selfTest/ }));

  const alert = await screen.findByRole('alert');
  // Visible, actionable text; the raw cause sits in the keyboard-reachable disclosure.
  expect(alert.textContent).toBe('engines.selfTestUnreachable');
  expect(screen.getByText('common.details').tagName).toBe('SUMMARY');
  expect(screen.getByText('backend offline').closest('details')).not.toBeNull();
});
