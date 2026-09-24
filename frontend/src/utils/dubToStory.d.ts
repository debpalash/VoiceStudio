export function dubToStory(
  segments:
    | {
        text?: unknown;
        start?: unknown;
        speaker_id?: unknown;
        profile_id?: unknown;
        merge_parts?: unknown;
      }[]
    | null
    | undefined,
  options?: {
    profiles?: { id?: unknown; name?: unknown }[] | null;
    unknownSpeakerLabel?: string;
  },
): {
  cast: { id: string; name: string; profileId: string | null; color?: string }[];
  tracks: { character: string; text: string }[];
  stats: { lines: number; speakers: number; skippedEmpty: number };
};
