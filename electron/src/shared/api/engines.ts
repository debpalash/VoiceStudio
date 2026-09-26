import { apiJson, apiPost } from './client';
import type {
  AllEnginesResponse,
  EngineFamily,
  EngineDiskUsage,
  EngineHealthResponse,
  EngineSelfTestResponse,
  SelectEngineResponse,
} from './types';

interface TranslationEngine {
  id: string;
  display_name: string;
  pip_package: string | null;
  probe_module: string | null;
  category: 'offline' | 'online' | 'llm';
  needs_key: boolean;
  builtin?: boolean;
  notes?: string;
  installed: boolean;
  availability_reason: string;
  /** `uv pip install <pkg>` (single-sourced by the backend registry), or
   *  null when the engine needs no separate install (builtin/core dep). */
  install_command: string | null;
}
export interface TranslationEnginesResponse {
  engines: TranslationEngine[];
  sandboxed: boolean;
}
export interface InstallEngineResponse {
  status:
    | 'installed'
    | 'already_installed'
    | 'installed_but_probe_failed'
    | 'uninstalled'
    | 'no_op';
  engine: string;
  package?: string;
  log_tail?: string;
  restart_required?: boolean;
}

export async function listEngines(): Promise<AllEnginesResponse> {
  return apiJson<AllEnginesResponse>('/engines');
}

export async function selectEngine(
  family: EngineFamily,
  backendId: string,
  modelId?: string,
): Promise<SelectEngineResponse> {
  return apiPost<SelectEngineResponse>('/engines/select', {
    family,
    backend_id: backendId,
    // Only mlx-audio's curated-model picker (#981) sets this — omit
    // entirely rather than send `undefined`/null for every other call site.
    ...(modelId ? { model_id: modelId } : {}),
  });
}

/**
 * Plan 02-04 / ENGINE-06 — spawn-and-ping a SubprocessBackend (or
 * `is_available()`-check an in-process backend) on user demand. The
 * Engine Compatibility Matrix's "Test engine" button calls this; never
 * called on Settings mount to avoid auto-spawning every sidecar.
 *
 * The endpoint never 500s on a sick backend — it captures the exception
 * into the response body as `{ ok: false, message: "ExcType: ..." }`.
 * 404 is returned only when `engineId` matches none of the tts/asr/llm
 * registries.
 */
export async function getEngineHealth(engineId: string): Promise<EngineHealthResponse> {
  return apiJson<EngineHealthResponse>(`/engines/${encodeURIComponent(engineId)}/health`);
}

export async function getEngineDiskUsage(engineId: string): Promise<EngineDiskUsage> {
  return apiJson<EngineDiskUsage>(`/engines/${encodeURIComponent(engineId)}/disk-usage`);
}

/**
 * Run a bounded, real tiny-synthesis on an AVAILABLE, IN-PROCESS TTS engine —
 * proves the engine actually emits audio (duration + sample-rate + samples),
 * not just that its package imports (`is_available()` liveness). The Compat
 * Matrix's "Self-test" button calls this; only ever on user click, never on
 * Settings mount. 400 for a subprocess-isolated or not-available engine, 404
 * for a non-TTS id. Never 500s on a synth failure — it lands in `ok:false`.
 */
export async function selfTestEngine(engineId: string): Promise<EngineSelfTestResponse> {
  return apiPost<EngineSelfTestResponse>(`/engines/${encodeURIComponent(engineId)}/selftest`, {});
}

// ── One-click sidecar-engine install (IndexTTS-2 & friends) ─────────────

export type SidecarStepState = 'pending' | 'running' | 'done' | 'skipped' | 'error';

export interface SidecarInstallStep {
  id: string;
  state: SidecarStepState;
  detail: string | null;
}

export interface SidecarInstallJob {
  engine_id: string;
  state: 'running' | 'succeeded' | 'failed';
  steps: SidecarInstallStep[];
  log: string[];
  error: string | null;
  remediation: string | null;
  weights_progress: {
    filename: string | null;
    downloaded: number | null;
    total: number | null;
    pct: number | null;
  } | null;
  started_at: number;
  finished_at: number | null;
}

export interface SidecarInstallStatus {
  engine_id: string;
  installed: boolean;
  managed: boolean;
  install_dir: string | null;
  job: SidecarInstallJob | null;
}

export interface SidecarInstallStartResponse {
  status: 'started' | 'already_running' | 'already_installed';
  engine: string;
}

/** Start the resumable one-click install for a sidecar engine (IndexTTS-2).
 *  Idempotent: re-POSTing while a job runs returns `already_running`; a
 *  healthy install returns `already_installed`; a partial install repairs. */
export async function installSidecarEngine(engineId: string): Promise<SidecarInstallStartResponse> {
  return apiPost<SidecarInstallStartResponse>(
    `/engines/sidecar/${encodeURIComponent(engineId)}/install`,
    {},
  );
}

/** Poll the sidecar install job — step-by-step states + log tail + error
 *  with remediation. Cheap (file probes only), safe to poll every ~1.5 s. */
export async function getSidecarInstallStatus(engineId: string): Promise<SidecarInstallStatus> {
  return apiJson<SidecarInstallStatus>(
    `/engines/sidecar/${encodeURIComponent(engineId)}/install/status`,
  );
}

export async function listTranslationEngines(): Promise<TranslationEnginesResponse> {
  return apiJson<TranslationEnginesResponse>('/engines/translation');
}

export async function installTranslationEngine(id: string): Promise<InstallEngineResponse> {
  return apiPost<InstallEngineResponse>(`/engines/translation/${id}/install`, {});
}

// ── Effect presets ──────────────────────────────────────────────────────

export interface EffectPreset {
  id: string;
  label: string;
  icon: string;
  description: string;
}
