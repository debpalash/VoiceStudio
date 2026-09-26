import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the client layer so we assert URL/query/body construction, not network.
const apiFetch = vi.fn();
const apiPost = vi.fn();
vi.mock('./client', () => ({
  apiFetch: (...a: unknown[]) => apiFetch(...a),
  apiPost: (...a: unknown[]) => apiPost(...a),
  apiJson: vi.fn(),
}));

import { exportPersona, importPersona, inspectPersona } from './profiles';

beforeEach(() => {
  apiFetch.mockReset();
  apiPost.mockReset();
});

describe('exportPersona', () => {
  it('POSTs with no query when no opts', async () => {
    apiFetch.mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(['z'])) });
    await exportPersona('abc');
    expect(apiFetch).toHaveBeenCalledWith('/personas/export/abc', { method: 'POST' });
  });

  it('builds the query string for license/tags/include_reference', async () => {
    apiFetch.mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(['z'])) });
    await exportPersona('abc', {
      license_spdx: 'CC-BY-4.0',
      tags: 'a,b',
      include_reference: false,
    });
    const url = apiFetch.mock.calls[0][0] as string;
    expect(url).toContain('license_spdx=CC-BY-4.0');
    expect(url).toContain('tags=a%2Cb');
    expect(url).toContain('include_reference=false');
  });

  it('omits include_reference when true (default)', async () => {
    apiFetch.mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(['z'])) });
    await exportPersona('abc', { include_reference: true });
    expect(apiFetch.mock.calls[0][0]).not.toContain('include_reference');
  });

  it('throws the status code on a non-ok response', async () => {
    apiFetch.mockResolvedValue({ ok: false, status: 503, blob: vi.fn() });
    await expect(exportPersona('abc')).rejects.toThrow('503');
  });

  it('returns the blob on success', async () => {
    const blob = new Blob(['data']);
    apiFetch.mockResolvedValue({ ok: true, blob: () => Promise.resolve(blob) });
    expect(await exportPersona('abc')).toBe(blob);
  });
});

describe('importPersona / inspectPersona', () => {
  it('importPersona posts the FormData to /personas/import', async () => {
    apiPost.mockResolvedValue({ success: true });
    const fd = new FormData();
    fd.append('file', new Blob(['z']), 'x.ovsvoice');
    await importPersona(fd);
    expect(apiPost).toHaveBeenCalledWith('/personas/import', fd);
  });

  it('inspectPersona posts the FormData to /personas/inspect', async () => {
    apiPost.mockResolvedValue({ format: 'ovsvoice' });
    const fd = new FormData();
    fd.append('file', new Blob(['z']), 'x.ovsvoice');
    await inspectPersona(fd);
    expect(apiPost).toHaveBeenCalledWith('/personas/inspect', fd);
  });
});
