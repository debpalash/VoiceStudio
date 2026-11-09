export interface BatchJob {
  id: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  filename: string;
  langs: string[];
  voice_id?: string;
  preserve_bg: boolean;
  translation_provider?: string;
  created_at: number;
  started_at?: number;
  finished_at?: number;
  error?: string;
  /** Failure class from the backend taxonomy (e.g. NO_AUDIO_TRACK), for a localized message. */
  docs_topic?: string | null;
  attempts?: number;
  retry_ready?: boolean;
  setup_required?:
    | {
        kind: 'argos_packs';
        source_lang: string;
        target_langs: string[];
      }
    | {
        kind: 'model_licence_required';
        code?: string;
        message?: string;
        models?: { repo_id: string; license?: string | null; category?: string; fingerprint: string }[];
      };
  progress?: {
    stage: string;
    percent: number;
    current_lang?: string;
    current_segment?: number;
    total_segments?: number;
    segments_count?: number;
  };
  outputs?: Record<string, string>;
}
