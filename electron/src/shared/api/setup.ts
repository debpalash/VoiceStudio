import { API, apiJson, apiPost, apiFetch } from './client';

interface MissingModel {
  repo_id: string;
  label: string;
}

export interface SetupStatus {
  models_ready: boolean;
  missing: MissingModel[];
  hf_cache_dir: string;
  disk_free_gb: number;
  min_free_gb: number;
  enough_disk: boolean;
}

export async function setupStatus(): Promise<SetupStatus> {
  return apiJson<SetupStatus>('/setup/status');
}

export function setupDownloadStreamUrl(): string {
  return `${API}/setup/download-stream`;
}

// ── Model store ───────────────────────────────────────────────────────────

interface KnownModel {
  repo_id: string;
  label: string;
  role: 'TTS' | 'ASR' | 'Diarisation' | string;
  size_gb: number;
  required: boolean;
  note?: string;
  installed: boolean;
  /** Truncated download on disk (config landed, weight shard didn't). */
  incomplete?: boolean;
  size_on_disk_bytes: number;
  nb_files: number;
  /** False when the model can't run on this host (`platforms` in models.yaml). */
  supported?: boolean;
  /** Curated "best for your system" pick (`curated_on` in models.yaml) —
   *  drives the recommended badge in the wizard and Settings model store. */
  curated?: boolean;
  platforms?: string[];
  /** Dictation runtime marker (`engine: sherpa-onnx` in models.yaml). */
  engine?: string;
  dictation_id?: string;
  /** Dictation mode: 'offline' | 'streaming'. */
  tag?: string;
}

export interface ModelList {
  models: KnownModel[];
  total_installed_bytes: number;
  hf_cache_dir: string;
  /** Free space on the cache volume — surfaced in the Model Store header so an
   *  "Install all" can't silently overrun the disk. */
  disk_free_gb?: number;
  /** Host platform tags (e.g. ['darwin', 'darwin-arm64']). */
  platform_tags?: string[];
}

export async function listModels(): Promise<ModelList> {
  return apiJson<ModelList>('/models');
}

export async function installModel(repo_id: string): Promise<{ status: string; repo_id: string }> {
  return apiPost('/models/install', { repo_id });
}

/** Request cancellation of an in-flight install (FDL-11). Best-effort: the
 *  backend stops further retries and emits an `install_cancelled` SSE event. */
export async function cancelInstallModel(repo_id: string): Promise<{ cancelling: string }> {
  return apiPost('/models/install/cancel', { repo_id });
}

// ── Device-aware model recommendation ─────────────────────────────────────

interface RecommendedModel {
  repo_id: string;
  label: string;
  role: string;
  size_gb: number;
  required: boolean;
  note: string | null;
  installed: boolean;
}

export interface Recommendations {
  device: {
    os: string;
    arch: string;
    is_mac_arm: boolean;
    is_mac_intel: boolean;
    is_linux: boolean;
    is_windows: boolean;
    has_cuda: boolean;
    label: string;
  };
  rationale: string;
  models: RecommendedModel[];
  download_gb_remaining: number;
  total_gb: number;
  all_installed: boolean;
}

export async function getRecommendations(): Promise<Recommendations> {
  return apiJson<Recommendations>('/setup/recommendations');
}

export async function deleteModel(
  repo_id: string,
): Promise<{ deleted: boolean; repo_id: string; freed_bytes: number }> {
  // HF repo_ids look like "owner/name" — encode each segment so special chars
  // are escaped but the literal "/" survives into FastAPI's `:path` converter.
  // encodeURIComponent on the whole string would turn "/" into "%2F", which
  // some ASGI middleware rejects as a path-traversal attempt.
  const path = repo_id.split('/').map(encodeURIComponent).join('/');
  const r = await apiFetch(`/models/${path}`, { method: 'DELETE' });
  return r.json();
}

// ── Pre-flight system check ───────────────────────────────────────────────

type CheckStatus = 'pass' | 'warn' | 'fail';

interface PreflightCheck {
  id: string;
  label: string;
  status: CheckStatus;
  detail: string;
  fix: string | null;
}

interface PreflightDevice {
  os: string;
  arch: string;
  gpu_vendor: 'nvidia' | 'amd' | 'apple' | 'intel' | 'unknown' | 'none';
  gpu_backend: 'cuda' | 'rocm' | 'mps' | 'cpu';
  gpu_available: boolean;
  gpu_driver: string | null;
  gpu_device_name: string | null;
  ram_gb: number;
  disk_free_gb: number;
}

export interface PreflightReport {
  ok: boolean;
  has_warnings: boolean;
  checks: PreflightCheck[];
  device: PreflightDevice;
}

export async function preflight(): Promise<PreflightReport> {
  return apiJson<PreflightReport>('/setup/preflight');
}
