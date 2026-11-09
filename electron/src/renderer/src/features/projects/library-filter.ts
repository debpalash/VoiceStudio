export type LibraryKind =
  | 'dub'
  | 'stories'
  | 'audiobooks'
  | 'profiles'
  | 'transcripts'
  | 'history'
  | 'exports';
export type Filter = 'all' | LibraryKind;

/** Work you open and keep editing; everything else is library material made along the way. */
export const PROJECT_KINDS: readonly LibraryKind[] = ['dub', 'stories', 'audiobooks'];
export const LIBRARY_KINDS: readonly LibraryKind[] = [
  'profiles',
  'transcripts',
  'history',
  'exports',
];

/** "All projects" lists projects only; takes, voices, transcripts and exports keep their own lists. */
export function matchesFilter(kind: LibraryKind, filter: Filter): boolean {
  return filter === 'all' ? PROJECT_KINDS.includes(kind) : kind === filter;
}

export function countByFilter(kinds: readonly LibraryKind[]): Record<Filter, number> {
  const counts = Object.fromEntries(
    (['all', ...PROJECT_KINDS, ...LIBRARY_KINDS] as Filter[]).map((filter) => [filter, 0]),
  ) as Record<Filter, number>;
  for (const kind of kinds) {
    counts[kind]++;
    if (matchesFilter(kind, 'all')) counts.all++;
  }
  return counts;
}
