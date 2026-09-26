export function applyVdState(
  states: Record<string, string>,
  category: string,
  value: string,
): { vdStates: Record<string, string>; clearedCategory: string | null };
export function buildDesignInstruct(
  states: Record<string, string>,
  free: string,
): { instruct: string; unsupported: string[]; duplicates: string[]; conflicts: string[] };
export function mergeDescribedAttrs(attrs?: Record<string, string>): Record<string, string>;
export function instructToVdStates(instruct?: string): Record<string, string>;
export function designModeProfileId(
  id: string | null,
  profiles: { id: string; kind: string }[],
): string | null;
export function instructToFormValue(
  instruct: string | { instruct?: string } | null | undefined,
): string;
