import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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

it('shows a request failure as an alert', async () => {
  mock.api.mockRejectedValue('backend offline');
  render(<GgufSelfTest />);
  fireEvent.click(screen.getByRole('button', { name: /engines.selfTest/ }));

  const alert = await screen.findByRole('alert');
  expect(alert.getAttribute('title')).toBe('backend offline');
});
