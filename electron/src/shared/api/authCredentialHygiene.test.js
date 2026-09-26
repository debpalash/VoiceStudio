import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SOURCE_EXTENSIONS = new Set(['.js', '.jsx', '.ts', '.tsx']);

function* productionFiles(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'test') continue;
      yield* productionFiles(absolute);
      continue;
    }
    if (SOURCE_EXTENSIONS.has(path.extname(entry.name)) && !/\.test\.[jt]sx?$/.test(entry.name)) {
      yield absolute;
    }
  }
}

const sources = () =>
  [...productionFiles(SRC)].map((file) => ({
    file: path.relative(SRC, file).replaceAll('\\', '/'),
    source: fs.readFileSync(file, 'utf8'),
  }));

// Any storage receiver counts: `sessionStorage.setItem('ov_api_key', …)` is the
// same credential-persistence class as localStorage, and production code passes
// injected stores under other names (sessionStore, localStore, legacyStorage,
// storage). Matching `.setItem(<master key>` — whatever the receiver, whatever
// the quote style, optional chaining included — closes the whole class instead
// of one spelling. getItem/removeItem (the migration/removal call sites) and
// setItem of other keys stay legal.
const PERSISTED_MASTER_RE =
  /\.setItem(?:\?\.)?\(\s*(?:LS_API_KEY\b|LEGACY_API_KEY_STORAGE_KEY\b|[`'"]ov_api_key[`'"])/;

describe('administrator credential hygiene static guard', () => {
  it('has no production path that writes the legacy master key to any Web Storage', () => {
    const violations = sources()
      .filter(({ source }) => PERSISTED_MASTER_RE.test(source))
      .map(({ file }) => file);

    expect(violations, 'OMNIVOICE_API_KEY must never enter localStorage or sessionStorage').toEqual(
      [],
    );
  });

  it('catches realistic storage receivers, aliases, and quote styles', () => {
    const caught = [
      "localStorage.setItem('ov_api_key', key)",
      "localStorage.setItem?.('ov_api_key', key)",
      'sessionStorage.setItem("ov_api_key", key)',
      'window.localStorage.setItem(`ov_api_key`, key)',
      'sessionStore?.setItem(LS_API_KEY, key)',
      'localStore.setItem( LEGACY_API_KEY_STORAGE_KEY, key)',
      'legacyStorage?.setItem(LS_API_KEY, master)',
    ];
    const allowed = [
      "localStorage.removeItem('ov_api_key')",
      'localStore?.getItem(LS_API_KEY)',
      'storage.setItem(ADMIN_SESSION_STORAGE_KEY, JSON.stringify(record))',
      "sessionStore?.setItem('ov_pin', pin)",
      'localStorage.setItem(LS_BACKEND_URL, normalized)',
    ];
    for (const line of caught) expect(PERSISTED_MASTER_RE.test(line), line).toBe(true);
    for (const line of allowed) expect(PERSISTED_MASTER_RE.test(line), line).toBe(false);
  });

  it('has no production WebSocket query builder for a master API key', () => {
    const forbidden = [
      /searchParams\.set\(\s*['"]api_key['"]/,
      /[?&]api_key=\$\{/,
      /[?&]api_key=['"]\s*\+/,
    ];
    const violations = sources()
      .filter(({ source }) => forbidden.some((pattern) => pattern.test(source)))
      .map(({ file }) => file);

    expect(violations, 'WebSocket URLs may contain ws_ticket, never a master key').toEqual([]);
  });

  it('keeps every WebSocket consumer behind the authenticated URL boundary', () => {
    const constructors = sources()
      .filter(({ source }) => source.includes('new WebSocket('))
      .map(({ file, source }) => ({ file, authenticated: source.includes('authenticatedWsUrl') }));

    expect(constructors).toEqual([
      { file: 'components/CaptureWidget.jsx', authenticated: true },
      { file: 'hooks/useDubLivePreview.js', authenticated: true },
      { file: 'hooks/useRealtimeEvents.js', authenticated: true },
    ]);
  });
});
