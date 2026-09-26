export function buildAutoCast(
  text: string,
  cast?: { id: string; name: string; profileId: string | null; color?: string }[],
  profiles?: { id: string }[],
): {
  cast: { id: string; name: string; profileId: string | null; color?: string }[];
  tracks: { character: string; text: string }[];
  speakers: string[];
};
