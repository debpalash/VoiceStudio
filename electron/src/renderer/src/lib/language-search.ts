/** Search, ranking and row layout for the language menu. Pure: no React, no storage. */

export interface SearchOption {
  value: string;
  label: string;
  search: string[];
  disabled?: boolean;
  pinned?: boolean;
  /** Primary language of a regional or script variant (e.g. `pt` for pt-BR). */
  family?: string;
  /** Localized name of `family`, shown above grouped variants. */
  familyLabel?: string;
}

export const normalizeSearch = (value: string) =>
  value.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase().trim();

export function rankOptions(options: SearchOption[], query: string, recents: string[]) {
  const normalized = normalizeSearch(query);
  const terms = normalized.split(/\s+/).filter(Boolean);
  return options
    .map((option, order) => {
      const fields = option.search;
      const exact = fields.some((field) => field === normalized);
      const matches = terms.every((term) => fields.some((field) => field.includes(term)));
      const prefix = fields.some((field) => field.startsWith(normalized));
      const recent = recents.indexOf(option.value);
      return {
        option,
        order,
        matches,
        score: terms.length
          ? exact
            ? 0
            : prefix
              ? 1
              : 2
          : option.pinned
            ? -2
            : recent < 0
              ? 10
              : recent,
      };
    })
    .filter((item) => item.matches)
    .sort(
      (a, b) =>
        Number(Boolean(a.option.disabled)) - Number(Boolean(b.option.disabled)) ||
        a.score - b.score ||
        a.order - b.order,
    )
    .map(({ option }) => option);
}

/** Keyboard/active value of the row that reveals or hides unsupported languages. */
export const MORE_ROW = '\u0000more';

export type MenuRow =
  | { kind: 'header'; key: string; label: string }
  | { kind: 'family'; key: string; label: string }
  | { kind: 'items'; key: string; items: SearchOption[]; start: number }
  | { kind: 'more'; key: string; hidden: number; expanded: boolean; position: number };

export interface MenuLayout {
  rows: MenuRow[];
  /** Enabled options plus the more row, in visual (reading) order. */
  navigation: string[];
  /** Options the list renders, in visual order. */
  visible: SearchOption[];
  /** Number of option rows (languages plus the toggle row), for aria-setsize. */
  size: number;
}

export interface MenuLabels {
  recent: string;
  available: string;
  unavailable: string;
}

/**
 * Calm default layout: Auto, recent choices, then the usable languages.
 * Unsupported languages collapse behind one row until expanded or searched,
 * and variants that share a language (pt-BR / pt-PT) sit under that language.
 */
export function layoutMenu({
  sorted,
  query,
  recents,
  columns,
  expanded,
  toggle = true,
  labels,
}: {
  sorted: SearchOption[];
  query: string;
  recents: string[];
  columns: number;
  expanded: boolean;
  /** False when unsupported languages must stay shown (a saved unsupported choice). */
  toggle?: boolean;
  labels: MenuLabels;
}): MenuLayout {
  const rows: MenuRow[] = [];
  const navigation: string[] = [];
  const visible: SearchOption[] = [];
  let size = 0;
  const searching = normalizeSearch(query) !== '';
  const enabled = sorted.filter((item) => !item.disabled);
  const unavailable = sorted.filter((item) => item.disabled);
  const pinned = searching ? [] : enabled.filter((item) => item.pinned);
  const recent = searching
    ? []
    : enabled.filter((item) => !item.pinned && recents.includes(item.value));
  const rest = enabled.filter((item) => !pinned.includes(item) && !recent.includes(item));

  const pushItems = (items: SearchOption[], key: string) => {
    for (let index = 0; index < items.length; index += columns) {
      const chunk = items.slice(index, index + columns);
      rows.push({
        kind: 'items',
        key: `${key}:${chunk[0].value}`,
        items: chunk,
        start: size,
      });
      for (const item of chunk) {
        visible.push(item);
        size += 1;
        if (!item.disabled) navigation.push(item.value);
      }
    }
  };
  const pushSection = (items: SearchOption[], key: string, label?: string) => {
    if (!items.length) return;
    if (label) rows.push({ kind: 'header', key: `${key}-header`, label });
    for (const part of groupFamilies(items)) {
      if (part.label)
        rows.push({ kind: 'family', key: `${key}-family:${part.key}`, label: part.label });
      pushItems(part.items, key);
    }
  };

  pushSection(pinned, 'pinned');
  pushSection(recent, 'recent', labels.recent);
  pushSection(
    rest,
    'available',
    recent.length || unavailable.length ? labels.available : undefined,
  );
  if (unavailable.length) {
    if (!searching && toggle) {
      rows.push({
        kind: 'more',
        key: 'more',
        hidden: unavailable.length,
        expanded,
        position: size,
      });
      size += 1;
      navigation.push(MORE_ROW);
    }
    if (searching || expanded || !toggle)
      pushSection(unavailable, 'unavailable', labels.unavailable);
  }
  return { rows, navigation, visible, size };
}

/** Split a section into runs, pulling each multi-variant family together at its first position. */
function groupFamilies(items: SearchOption[]) {
  const counts = new Map<string, number>();
  for (const item of items)
    if (item.family) counts.set(item.family, (counts.get(item.family) ?? 0) + 1);
  const parts: { key: string; label?: string; items: SearchOption[] }[] = [];
  const done = new Set<string>();
  let run: SearchOption[] = [];
  const flush = () => {
    if (run.length) parts.push({ key: `run${parts.length}`, items: run });
    run = [];
  };
  for (const item of items) {
    const family = item.family;
    if (!family || (counts.get(family) ?? 0) < 2) {
      run.push(item);
      continue;
    }
    if (done.has(family)) continue;
    done.add(family);
    flush();
    const members = items.filter((other) => other.family === family);
    parts.push({ key: family, label: item.familyLabel || family, items: members });
  }
  flush();
  return parts;
}
