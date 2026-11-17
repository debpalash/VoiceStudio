/**
 * Every IPC handler sits behind one trust gate.
 *
 * `fix/ipc` had to retrofit a sender check onto the file and window handlers, and
 * by then five modules had each written their own copy of the predicate — site
 * browser, watch folders, repair agents, native capture and the updater — with
 * their own message and no test that a *new* channel gets a check at all. The
 * predicate now lives in `trusted-ipc.ts`; these tests keep it that way.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const MAIN = fileURLToPath(new URL('.', import.meta.url));

function sources(dir: string = MAIN, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      sources(full, out);
      continue;
    }
    if (name.endsWith('.ts') && !name.endsWith('.test.ts') && !name.endsWith('.d.ts')) out.push(full);
  }
  return out;
}

const FILES = sources();
const pathOf = (file: string): string => relative(MAIN, file).split('\\').join('/');
const read = (file: string): string => readFileSync(file, 'utf8');

/** Modules that register a channel with `ipcMain` directly or through the gate. */
const REGISTERING_MODULES = new Set([
  'account/account-ipc.ts',
  'index.ts',
  'ipc.ts',
  'native-capture.ts',
  'repair-agents.ts',
  'site-browser.ts',
  'trusted-ipc.ts',
  'updater.ts',
  'watch-folders.ts',
]);

// `ipc.ts` re-exports the assertion, so importing it from there is the same gate.
const TRUST_IMPORT = /from '\.\.?\/(?:trusted-ipc|ipc)'/;


interface Registration {
  channel: string;
  body: string;
}

/** Index just past the bracket that closes the one at `open`. */
function closing(text: string, open: number, opening: string, closingCharacter: string): number {
  let depth = 0;
  for (let index = open; index < text.length; index++) {
    if (text[index] === opening) depth++;
    else if (text[index] === closingCharacter) {
      depth--;
      if (depth === 0) return index + 1;
    }
  }
  return text.length;
}

/** A body with its comments removed, so `// guard(event)` cannot count as a guard. */
function code(body: string): string {
  return body.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
}

/**
 * Each `ipcMain.handle`/`ipcMain.on` call: the channel expression and the
 * callback body, bounded by the call itself.
 *
 * The body has to stop where the call stops. A concise callback
 * (`(event) => call(event)`) used to take a fixed window of the file, which could
 * reach into the next registration and borrow its guard.
 */
function registrations(text: string): Registration[] {
  const found: Registration[] = [];
  for (const match of text.matchAll(/ipcMain\.(?:handle|on)\(/g)) {
    const callOpen = (match.index ?? 0) + match[0].length - 1;
    const callEnd = closing(text, callOpen, '(', ')');
    const comma = text.indexOf(',', callOpen + 1);
    const channel = comma === -1 ? '' : text.slice(callOpen + 1, comma).trim();
    const arrow = text.indexOf('=>', callOpen);
    if (arrow === -1 || arrow >= callEnd) {
      found.push({ channel, body: text.slice(callOpen, callEnd) });
      continue;
    }
    const braceStart = text.indexOf('{', arrow);
    const body =
      braceStart === -1 || braceStart >= callEnd
        ? text.slice(arrow, callEnd)
        : text.slice(braceStart, closing(text, braceStart, '{', '}'));
    found.push({ channel, body });
  }
  return found;
}

/**
 * What a handler body does to authorize the caller: the gate, a module helper
 * that calls it, or a `trustedOwner` result that is actually used.
 *
 * A bare `event.sender ===` comparison is deliberately *not* accepted: it says
 * which webContents sent the message but not which frame inside it, so a subframe
 * of the right window would pass. The one handler that authorizes that way (the
 * persistence flush) is exempted by channel instead.
 *
 * Calling `trustedOwner(...)` and ignoring what it returns authorizes nothing, so
 * the result has to be kept (`const win = trustedOwner(...)`) or dereferenced
 * (`trustedOwner(...)?.minimize()`).
 */
const GUARDED_BODY =
  /\b(?:assertTrustedMainFrame|assertTrustedTopLevel|assertMain|assertRecorder|trusted|guard|entryFor|requireTrusted)\s*\(|=\s*trustedOwner\(|trustedOwner\([^\n]*?\)\s*\??\./;

/**
 * Registrations whose handler authorizes without the shared predicate.
 *
 * The persistence flush is a one-shot listener: it matches a per-call token and
 * checks `event.sender` against the window it asked, so another frame cannot
 * drive it. It is also a named function reference, which the body scan cannot
 * follow, so it is exempt by channel.
 */
const GATED_WITHOUT_THE_PREDICATE = ['PERSISTENCE_FLUSHED'];

/** Modules that own registration: `ipc.ts` is the dispatch wrapper every `handle(...)` goes through. */
const DISPATCH_MODULES = new Set(['ipc.ts', 'trusted-ipc.ts']);

describe('IPC trust gate', () => {
  it('keeps the list of registering modules explicit', () => {
    const found = FILES.filter((file) => /ipcMain\.(handle|on)\(/.test(read(file))).map(pathOf);
    expect(found.sort()).toEqual([...REGISTERING_MODULES].sort());
  });

  it('routes every registering module through the shared assertion', () => {
    const missing = FILES.filter((file) => /ipcMain\.(handle|on)\(/.test(read(file)))
      .filter((file) => pathOf(file) !== 'trusted-ipc.ts')
      .filter((file) => !TRUST_IMPORT.test(read(file)))
      .map(pathOf);
    expect(missing).toEqual([]);
  });

  it('guards every registration body', () => {
    // An import at the top of a module proves nothing: `watch:scan` losing its
    // `guard(event)` call would leave the module importing the gate while that
    // handler answers any frame. Each callback body is inspected instead.
    const ungated: string[] = [];
    for (const file of FILES) {
      const relative = pathOf(file);
      if (DISPATCH_MODULES.has(relative)) continue;
      const text = read(file);
      registrations(text).forEach(({ channel, body }, index) => {
        if (GUARDED_BODY.test(code(body))) return;
        // The exemption covers its own registration only: another handler in the
        // same file still has to carry a check.
        if (GATED_WITHOUT_THE_PREDICATE.some((name) => channel.includes(name))) return;
        ungated.push(`${relative}: registration #${index + 1} (${channel}) has no authorization call`);
      });
    }
    expect(ungated).toEqual([]);
  });

  it('has exactly one implementation of the frame comparison', () => {
    // `event.senderFrame !== ...` is the predicate itself; a second copy is how
    // the five modules drifted apart in the first place.
    const copies = FILES.filter((file) => /event\.senderFrame !==/.test(read(file))).map(pathOf);
    expect(copies).toEqual(['trusted-ipc.ts']);
  });

  it('gates both registration kinds through the wrapper', () => {
    const gate = read(join(MAIN, 'trusted-ipc.ts'));
    expect(gate).toContain('export function handleTrusted');
    expect(gate).toContain('export function onTrusted');
    // Both wrappers assert before the handler runs.
    expect(gate.match(/assertTrustedTopLevel\(event\)/g) ?? []).toHaveLength(2);
  });
});