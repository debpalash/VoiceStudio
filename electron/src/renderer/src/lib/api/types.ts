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
  /** Complete native Voice Design prompt, explicit picks and authoring engine. */
  design_recipe?: string | null;
  /** Epoch seconds (SQLite REAL) on the wire; tolerate ISO strings too. */
  created_at: number | string;
  is_locked: boolean | number | null;
  verified_own_voice?: boolean | number | null;
  consent_text?: string | null;
  consent_recorded_at?: string | null;
  /** Sync consent attestation (S2): required before a voice can sync. */
  consent_basis?: 'own_voice' | 'speaker_permission' | '' | null;
  consent_attested_at?: string | null;
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
  /** JSON copy of the Voice Design draft behind a design take; null for any other take or a row saved earlier. */
  design_recipe?: string | null;
  /** The take's text as a reference transcript: no script markup, '' when it may be cut short (#3062). */
  transcript?: string;
  /** Epoch seconds (SQLite REAL) on the wire; tolerate ISO strings too. */
  created_at: number | string;
}

// ── Engines (`GET /engines`) ───────────────────────────────────────────────
export interface EngineBackend {
  output_sample_rate?: number | null;
  output_channels?: number | null;
  /** Lowercase picker names; null/absent means model-specific or unrestricted. */
  supported_language_names?: string[] | null;
  /** Regional accents the engine renders itself, e.g. ['pt-BR'] (#3143). */
  rendered_dialects?: string[] | null;
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
  /** Speech-to-speech conversion: Convert's "Keep my delivery" (#3421). */
  supports_voice_conversion?: boolean;
  /** Graded emotion conditioning (IndexTTS2): text, strength and vector. */
  supports_emotion?: boolean;
  /** Built-in voices a request may pick (`preset_voice`); empty or absent when the engine has none (#2938). */
  preset_voices?: string[];
  default_voice?: string | null;
  /** False: needs a reference clip, so Voice Design is unavailable. Null/absent: undeclared. */
  supports_voice_design?: boolean | null;
  /** Whether `instruct` must be one of a fixed tag list (OmniVoice) or may be any wording. */
  instruct_vocabulary?: InstructVocabulary;
  /** Emotion mechanism and inline reaction tags the engine renders (#3422). */
  expression_caps?: ExpressionCaps;
  /** Seconds of a clone reference the engine uses; null when not verified (#2281). */
  max_ref_seconds?: number | null;
  /** How that stretch is chosen from a longer clip. */
  ref_strategy?: 'best_window' | 'head' | 'full' | null;
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

/** `tags` = the fixed OmniVoice attribute list; `freeform` = any wording is passed through. */
export type InstructVocabulary = 'tags' | 'freeform';

/** "graded" = IndexTTS2 sliders/description; "direction" = written `instruct`; "none". */
export type EmotionControl = 'graded' | 'direction' | 'none';

export interface ExpressionCaps {
  emotion: EmotionControl;
  /** Inline tags the engine's tokenizer renders, e.g. `[laughter]`. */
  tags: string[];
}

/** What the client reports about its Voice Design draft. The server adds a
 *  `mapped` section of its own before storing it. */
export interface DesignRecipe {
  description: string;
  picks: Record<string, string>;
}

// ── Generation (`POST /generate`, classic whole-file path) ─────────────────
/** Everything the clone form sends. Names are the UI names; `toGenerateForm`
 *  in generate.ts maps them to the multipart field names. */
export interface CloneGenerateInput {
  /** Built-in voice id for engines that ship presets. */
  presetVoice?: string;
  wavBits?: 16 | 24 | 32;
  effectPreset?: 'broadcast' | 'raw';
  seed?: number;
  text: string;
  /** Display-name language ("English"). "Auto" is omitted on the wire. */
  language: string;
  /** Exactly one of these is set. */
  profileId?: string | null;
  refAudio?: File | Blob | null;
  refAudioName?: string;
  refText?: string;
  /** Style or design wording. It is filtered through the instruct whitelist
   *  unless `instructVocabulary` says the engine is free-form. */
  instruct?: string;
  /** The target engine's reading of `instruct`. Leave unset for the OmniVoice tag list. */
  instructVocabulary?: InstructVocabulary;
  /** Draft recorded next to a design take for later reopening; has no effect on the audio. */
  designRecipe?: DesignRecipe;
  /** IndexTTS2 independent emotion conditioning (text or eight weights). */
  emoText?: string;
  emoAlpha?: number;
  emoVector?: number[];
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
  /** Inline reaction tags the active engine will read as words (services/tag_policy).
   *  `tags` is capped at 8 on the wire; `count` is always exact. */
  tagWarning: { count: number; tags: string[] } | null;
}

// ── Errors ────────────────────────────────────────────────────────────────
export interface ApiErrorPayload {
  detail?: unknown;
  code?: string;
  [key: string]: unknown;
}
