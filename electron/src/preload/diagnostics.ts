/** Content-free diagnostic input. Main validates each value again before collection. */
export type DiagnosticCategory =
  | 'app_crash'
  | 'backend_crash'
  | 'renderer_crash'
  | 'install_failed'
  | 'install_stalled'
  | 'incompatibility'
  | 'generation_failed'
  | 'model_load_failed'
  | 'engine_failed'
  | 'download_failed'
  | 'operation_failed';

export type DiagnosticStage =
  | 'startup'
  | 'setup'
  | 'download'
  | 'verify'
  | 'install'
  | 'model_load'
  | 'generation'
  | 'transcription'
  | 'translation'
  | 'dub'
  | 'batch'
  | 'engine'
  | 'render'
  | 'shutdown'
  | 'other';

export interface DiagnosticInput {
  category: DiagnosticCategory;
  source?: 'renderer';
  stage?: DiagnosticStage;
  error_code?: string;
  error_type?: string;
  engine_id?: string;
  /** Already projected app-owned locations only; never a raw stack or source text. */
  frames?: string;
  reason?: string;
}

export interface DiagnosticState {
  enabled: boolean;
  opted_in: boolean;
  available: boolean;
  prompted: boolean;
  externally_managed?: boolean;
  pending_count?: number;
  dropped_count?: number;
}

export const DIAGNOSTIC_CHANNELS = {
  getStatus: 'diagnostics:getStatus',
  setConsent: 'diagnostics:setConsent',
  capture: 'diagnostics:capture',
} as const;
