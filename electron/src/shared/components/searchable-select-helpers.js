/**
 * Storage and list-shaping helpers for the shared picker.
 *
 * Kept beside the component rather than inside it: the component is already near
 * the 500-line cap in `.github/CONTRIBUTING.md`, and these two concerns (what the
 * browser stored, and which rows the menu shows) are pure functions that are
 * easier to reason about on their own.
 */

/** How many recent choices a picker remembers. */
export const MAX_RECENTS = 8;

/**
 * Read the saved recents for `key` as a list of strings.
 *
 * The value is raw `localStorage`: an older build, another page on the same
 * origin or a hand-edited devtools value can leave a number, an object or a
 * string there, and the caller iterates the result while rendering.
 */
export const readRecents = (key) => {
  if (!key || typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((entry) => typeof entry === 'string') : [];
  } catch {
    return [];
  }
};

/** Remember a choice, newest first, capped at {@link MAX_RECENTS}. */
export const writeRecents = (key, list) => {
  if (!key || typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(key, JSON.stringify(list.slice(0, MAX_RECENTS)));
  } catch {}
};

/**
 * Make sure the committed option stays among the rows the menu shows.
 *
 * The menu slices its list, so a saved value past the slice has no row at all.
 * Arrow keys then start wherever the first row happens to be, and Enter replaces
 * the saved value without the user choosing anything. Prepending the committed
 * option keeps the opening highlight on the value the user actually has.
 *
 * Skipped while a search query is active, where the visible list is the answer to
 * that query rather than the whole set.
 */
export const withCommittedOption = (
  rows,
  filtered,
  value,
  getValue,
  { query = '', pinnedValues = new Set() } = {},
) => {
  if (query || !value) return rows;
  // The pinned group already renders this value, so adding it to the main list
  // would show the same choice twice and make the arrows land on it again.
  if (pinnedValues.has(value)) return rows;
  if (rows.some((option) => getValue(option) === value)) return rows;
  const committed = filtered.find((option) => getValue(option) === value);
  return committed === undefined ? rows : [committed, ...rows];
};

/**
 * The pinned rows above the list: remembered choices first, then the popular ones.
 *
 * A row appears once even when it is both, and the group stops growing at twelve.
 * While a search query is active the pinned group is hidden, because the visible
 * list is the answer to that query.
 */
export const pinnedRows = ({ recents, popular, byValue, getValue, query = '' }) => {
  if (query) return [];
  const out = [];
  const seen = new Set();
  for (const value of recents) {
    const option = byValue.get(value);
    if (option && !seen.has(value)) {
      out.push({ o: option, kind: 'recent' });
      seen.add(value);
    }
    if (out.length >= 5) break;
  }
  for (const value of popular) {
    if (seen.has(value)) continue;
    const option = byValue.get(value);
    if (option) {
      out.push({ o: option, kind: 'popular' });
      seen.add(value);
    }
    if (out.length >= 12) break;
  }
  return out;
};
