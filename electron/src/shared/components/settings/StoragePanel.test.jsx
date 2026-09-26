import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn() } }));
const apiJson = vi.fn();
const apiFetch = vi.fn();
vi.mock('../../api/client', () => ({
  apiJson: (...args) => apiJson(...args),
  apiFetch: (...args) => apiFetch(...args),
}));
const invoke = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...args) => invoke(...args) }));

import StoragePanel from './StoragePanel';

describe('StoragePanel native path boundary', () => {
  it('never sends the selected models directory through HTTP', async () => {
    apiJson.mockResolvedValue({ configured: '', effective: '/cache', default: '/default' });
    apiFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ configured: '/private/models', restart_required: true }),
    });
    invoke.mockResolvedValue({ authorization: 'd'.repeat(64), path: '/private/models' });
    render(<StoragePanel />);

    await screen.findByTestId('models-dir-input');
    expect(screen.getByRole('heading', { name: 'Model cache' }).closest('section')).toHaveClass(
      'models-settings-compact',
      '!py-[9px]',
    );
    expect(screen.getByTestId('models-dir-input').closest('[data-slot="setting-row"]')).toHaveClass(
      '!py-[5px]',
    );
    fireEvent.click(screen.getByTestId('models-dir-save'));

    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith('authorize_host_path', {
        kind: 'models_dir',
        reset: false,
      }),
    );
    expect(apiFetch).toHaveBeenCalledWith(
      '/api/settings/storage/models-dir',
      expect.objectContaining({ body: JSON.stringify({ authorization: 'd'.repeat(64) }) }),
    );
    expect(apiFetch.mock.calls.flat().join(' ')).not.toContain('/private/models');
  });
});
