import { apiJson, apiFetch, apiPost } from './client';
import type { SystemInfo, ModelStatus, LogsResponse, ClearTauriResponse } from './types';

// ── Tauri IPC helpers ────────────────────────────────────────────────────
// Try native Tauri invoke() first — it's faster (no HTTP round-trip) and
// works when the Python backend is still booting. Falls back to HTTP when
// running in browser dev mode (no Tauri shell).

// ── System info (polled every 5s) ────────────────────────────────────────

export interface SysinfoData {
  cpu: number;
  ram: number;
  total_ram: number;
  vram: number;
  gpu_active: boolean;
}

// Cache VRAM from Python — it changes much slower than CPU/RAM, so we
// only refresh it every 15s instead of every 5s poll cycle.
let _vramCache: { vram: number; gpu_active: boolean; ts: number } | null = null;
const VRAM_CACHE_TTL = 15_000;

export async function sysinfo(): Promise<SysinfoData> {
  // Rust provides CPU + RAM; VRAM stays at 0. We merge with the Python
  // endpoint to get GPU data when available.
  const rustData = await apiJson<SysinfoData>('/sysinfo');

  // If we got data from Rust (vram=0), enrich with Python's VRAM data
  // but only re-fetch every 15s to avoid hammering the backend.
  if (rustData.vram === 0) {
    const now = Date.now();
    if (!_vramCache || now - _vramCache.ts > VRAM_CACHE_TTL) {
      try {
        const pyData = await apiJson<SysinfoData>('/sysinfo');
        _vramCache = { vram: pyData.vram, gpu_active: pyData.gpu_active, ts: now };
      } catch {
        // Python backend not ready yet — return Rust-only data
        return rustData;
      }
    }
    return {
      ...rustData,
      vram: _vramCache.vram,
      gpu_active: _vramCache.gpu_active,
    };
  }
  return rustData;
}

// ── Model status ─────────────────────────────────────────────────────────

export async function modelStatus(): Promise<ModelStatus> {
  return apiJson<ModelStatus>('/model/status');
}

// ── Loaded-model residency (MM2-04 endpoints) ────────────────────────────

/** One entry from GET /model/loaded — a model currently resident in memory.
 *  `engine_id`/`is_active_engine` attribute TTS-family entries to an engine
 *  (a model can stay resident after the user switches engines). */
export interface LoadedModel {
  id: string; // 'tts' | 'asr' | 'diarization' | 'sidecar:<e>' | 'engine:<e>' | 'capture-asr'
  name: string;
  checkpoint: string;
  device: string;
  vram_mb: number;
  unloadable: boolean;
  note?: string;
  engine_id?: string;
  is_active_engine?: boolean | null;
}

/** Free/total memory snapshot from GET /model/loaded. RAM is always present;
 *  VRAM fields appear only on a dedicated-GPU host; `warning` is a low-memory
 *  advisory string when free memory is below the headroom threshold. */
export interface SystemMemory {
  ram_available_gb?: number;
  ram_total_gb?: number;
  vram_free_gb?: number;
  vram_total_gb?: number;
  warning?: string;
}

export interface LoadedModelsResponse {
  models: LoadedModel[];
  count: number;
  system?: SystemMemory;
}

export async function listLoadedModels(): Promise<LoadedModelsResponse> {
  return apiJson<LoadedModelsResponse>('/model/loaded');
}

/** Unload one resident model by its /model/loaded `id`. The model reloads
 *  lazily on next use — unloading only frees memory, it never loses data. */
export async function unloadLoadedModel(modelId: string): Promise<unknown> {
  return apiPost(`/model/unload/${encodeURIComponent(modelId)}`);
}

// ── Audio cleaning ───────────────────────────────────────────────────────

export async function cleanAudio(formData: FormData): Promise<Response> {
  // Returns Response because caller needs blob body + X-Clean-Filename header.
  return apiFetch('/clean-audio', { method: 'POST', body: formData });
}

// ── System info (one-shot, for Settings) ─────────────────────────────────

export async function systemInfo(): Promise<SystemInfo> {
  return apiJson<SystemInfo>('/system/info');
}

// ── Notifications (polled by header bell + logs footer) ──────────────────

interface SystemNotification {
  id: string;
  level: 'info' | 'warn' | 'error';
  title?: string;
  message?: string;
  action?: { type: string; target: string; label?: string };
}

export interface NotificationsResponse {
  notifications: SystemNotification[];
}

export async function systemNotifications(): Promise<NotificationsResponse> {
  return apiJson<NotificationsResponse>('/system/notifications');
}

// ── Logs (polled every 5s) ───────────────────────────────────────────────

export async function systemLogs(tail: number = 300): Promise<LogsResponse> {
  return apiJson<LogsResponse>(`/system/logs?tail=${tail}`);
}

export async function systemLogsTauri(tail: number = 300): Promise<LogsResponse> {
  return apiJson<LogsResponse>(`/system/logs/tauri?tail=${tail}`);
}

// ── Log clearing ─────────────────────────────────────────────────────────

export async function clearSystemLogs(): Promise<unknown> {
  return apiPost('/system/logs/clear');
}

export async function clearTauriLogs(): Promise<ClearTauriResponse> {
  return apiPost<ClearTauriResponse>('/system/logs/tauri/clear');
}

// ── Memory flush ─────────────────────────────────────────────────────────

export async function flushMemory(unloadModel: boolean = false): Promise<unknown> {
  return apiPost(`/system/flush-memory?unload_model=${unloadModel}`);
}
