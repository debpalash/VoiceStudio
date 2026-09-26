import { apiJson, apiPost } from './client';

export async function listExportHistory(): Promise<unknown> {
  return apiJson('/export/history');
}

export async function exportAction(body: Record<string, unknown>): Promise<unknown> {
  return apiPost('/export', body);
}

export async function exportReveal(body: Record<string, unknown>): Promise<unknown> {
  if (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) {
    const { invoke } = await import('@tauri-apps/api/core');
    return invoke('reveal_host_path', { path: body.path });
  }
  return apiPost('/export/reveal', body);
}

export async function exportRecord(body: Record<string, unknown>): Promise<unknown> {
  return apiPost('/export/record', body);
}
