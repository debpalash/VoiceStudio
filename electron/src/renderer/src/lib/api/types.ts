/**
 * Wire types for the VoiceStudio backend (FastAPI, loopback 127.0.0.1:3900).
 *
 * Mirrors the backend pydantic / DB row shapes the clone page consumes. Keep
 * close to the wire: when the backend adds a field, add it here first so the
 * compiler flags every consumer.
 */

// ── Profiles (`GET /profiles`, raw voice_profiles rows) ────────────────────
export type ProfileKind = 'clone' | 'design';

export interface Profile {
  image_url?: string | null;
  /** Versioned `GET /profiles/{id}/audio` URL; changes when the clip is replaced. */
  audio_url?: string | null;
  id: string;
  name: string;
  kind: ProfileKind;
  ref_audio_path: string | null;
  locked_audio_path?: string | null;
  ref_text: string | null;
  instruct: string | null;
  language: string | null;
  seed: number | null;
  personality: string | null;
  vd_states: string | null;
  /** Epoch seconds (SQLite REAL) on the wire; tolerate ISO strings too. */
  created_at: number | string;
  is_locked: boolean | number | null;
  verified_own_voice?: boolean | number | null;
  consent_text?: string | null;
  consent_recorded_at?: string | null;
}

// ── History (`GET /history`) ───────────────────────────────────────────────
export interface HistoryItem {
  id: string;
  text: string;
  mode: string; // 'clone' | 'design' | ...
  language: string | null;
  instruct: string | null;
  profile_id: string | null;
  /** Bare filename under `/audio/`. */
  audio_path: string;
  duration_seconds: number | null;
  generation_time: number | null;
  seed: number | null;
  starred: boolean | number | null;
  /** Epoch seconds (SQLite REAL) on the wire; tolerate ISO strings too. */
  created_at: number | string;
}

// ── Engines (`GET /engines`) ───────────────────────────────────────────────
export interface EngineBackend {
  /** Lowercase picker names; null/absent means model-specific or unrestricted. */
  supported_language_names?: string[] | null;
  execution_evidence?: {
    evidence_state: string;
    actual_execution_provider?: string | null;
    actual_execution_device?: string | null;
  };
  curated_models?: { key: string; label: string; repo_id: string }[];
  id: string;
  display_name: string;
  available: boolean;
  reason: string | null;
  hint?: string | null;
  supports_cloning?: boolean | null;
  install_hint?: string | null;
  setup_snippet?: string | null;
  docs_url?: string | null;
  one_click_install?: boolean;
  local_install_required?: boolean;
  license_required?: boolean;
  license_accepted?: boolean;
  effective_device?: string;
  routing_status?: string;
  routing_reason?: string | null;
  isolation_mode?: 'in-process' | 'subprocess';
}

export interface EngineFamilyState {
  active_model?: string | null;
  backends: EngineBackend[];
  active: string | null;
  env_override?: string | null;
}

export interface EnginesResponse {
  tts: EngineFamilyState;
  asr: EngineFamilyState;
  llm: EngineFamilyState;
}

// ── System (`GET /system/info`) ────────────────────────────────────────────
export interface SystemInfo {
  app_version: string;
  python: string;
  platform: string;
  arch: string;
  device: string;
  data_dir: string;
  outputs_dir: string;
  model_checkpoint: string | null;
  asr_model: string | null;
  translate_provider?: string | null;
  has_hf_token?: boolean;
  os_version?: string;
  cpu_model?: string;
  cpu_count?: number;
  ram_total_gb?: number;
  gpu_name?: string;
  vram_total_gb?: number;
  disk_free_gb?: number;
  ffmpeg_ok?: boolean;
  ffmpeg_path?: string;
}

// ── Generation (`POST /generate`, classic whole-file path) ─────────────────
/** Everything the clone form sends. Names are the UI names; `toGenerateForm`
 *  in generate.ts maps them to the multipart field names. */
export interface CloneGenerateInput {
  seed?: number;
  text: string;
  /** Display-name language ("English"). "Auto" is omitted on the wire. */
  language: string;
  /** Exactly one of these is set. */
  profileId?: string | null;
  refAudio?: File | Blob | null;
  refAudioName?: string;
  refText?: string;
  /** Free-text style; sanitised through the instruct whitelist before sending. */
  instruct?: string;
  steps: number;
  cfg: number;
  speed: number;
  tShift: number;
  posTemp: number;
  classTemp: number;
  layerPenalty: number;
  denoise: boolean;
  postprocess: boolean;
  /** '' = auto. */
  duration: string;
}

export interface GenerateResult {
  blob: Blob;
  /** X-Audio-Id */
  id: string | null;
  /** X-Audio-Path: bare filename under `/audio/` */
  audioPath: string | null;
  durationSeconds: number | null;
  genTimeSeconds: number | null;
  seed: number | null;
  routing: { status: string; reason: string } | null;
  dropped: { count: number; text: string } | null;
}

// ── Errors ────────────────────────────────────────────────────────────────
export interface ApiErrorPayload {
  detail?: unknown;
  code?: string;
  [key: string]: unknown;
}
