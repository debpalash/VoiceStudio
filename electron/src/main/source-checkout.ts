import { existsSync, mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

/** Why a picked folder cannot be used for source repair. Shown to the user. */
export type SourceCheckoutProblem = 'installed_app' | 'not_git' | 'not_voicestudio' | 'read_only';

export type SourceCheckoutResult =
  | { ok: true; root: string }
  | { ok: false; reason: SourceCheckoutProblem };

// Files every VoiceStudio clone has had since the Electron migration.
const MARKERS = ['backend/main.py', 'electron/package.json', 'package.json'];

const hasMarkers = (dir: string) => MARKERS.every((entry) => existsSync(join(dir, entry)));
const isGitRoot = (dir: string) => existsSync(join(dir, '.git'));

function isInstalledApp(dir: string): boolean {
  // Packaged builds keep the app in resources/app.asar (Windows, Linux) or
  // VoiceStudio.app/Contents/Resources/app.asar (macOS). Neither is source.
  return (
    /\.app$/i.test(dir) ||
    existsSync(join(dir, 'resources', 'app.asar')) ||
    existsSync(join(dir, 'Contents', 'Resources', 'app.asar')) ||
    existsSync(join(dir, 'app.asar'))
  );
}

/** A real write probe: access(W_OK) ignores ACLs and folder attributes on Windows. */
function writable(dir: string): boolean {
  try {
    const probe = mkdtempSync(join(dir, '.voicestudio-write-check-'));
    rmSync(probe, { recursive: true, force: true });
    return true;
  } catch {
    return false;
  }
}

function ancestors(start: string): string[] {
  const out: string[] = [];
  let current = start;
  // Up to the filesystem root: a pick can sit arbitrarily deep, e.g. in node_modules.
  for (;;) {
    out.push(current);
    const parent = dirname(current);
    if (parent === current) return out;
    current = parent;
  }
}

function childCheckouts(dir: string): string[] {
  try {
    return readdirSync(dir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && !entry.name.startsWith('.'))
      .map((entry) => join(dir, entry.name))
      .filter((child) => isGitRoot(child) && hasMarkers(child));
  } catch {
    return [];
  }
}

/**
 * Resolve the folder a user picked to a writable VoiceStudio clone root.
 *
 * Accepts the clone itself, any folder inside it, and the folder `git clone`
 * ran in when it holds exactly one clone. Otherwise names the problem.
 */
export function inspectSourceCheckout(picked: string): SourceCheckoutResult {
  const start = resolve(picked);
  const chain = ancestors(start);
  // The nearest enclosing clone wins, even past a nested repository such as
  // a git dependency inside node_modules.
  let root = chain.find((dir) => isGitRoot(dir) && hasMarkers(dir));
  const gitRoot = chain.find(isGitRoot);
  if (!root) {
    // The folder `git clone` ran in (it may itself sit inside another repo,
    // such as a dotfiles home): accept it only when the choice is unambiguous.
    const children = childCheckouts(start);
    if (children.length === 1) root = children[0];
  }
  if (!root) {
    if (chain.some(isInstalledApp)) return { ok: false, reason: 'installed_app' };
    if (gitRoot) return { ok: false, reason: 'not_voicestudio' };
    return { ok: false, reason: hasMarkers(start) ? 'not_git' : 'not_voicestudio' };
  }
  if (!writable(root)) return { ok: false, reason: 'read_only' };
  return { ok: true, root };
}

export function isVoiceStudioCheckout(path: string): boolean {
  const result = inspectSourceCheckout(path);
  return result.ok && result.root === resolve(path);
}
