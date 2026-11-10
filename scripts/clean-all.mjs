// Dependency-free: this must still run after node_modules and .venv are gone.
import { execFileSync } from 'node:child_process';
import { existsSync, lstatSync, readdirSync, realpathSync, rmSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const GENERATED = new Set([
  'node_modules', '.venv', 'venv', '__pycache__', '.pytest_cache', '.mypy_cache',
  '.ruff_cache', '.cache', '.uv-cache', '.tox', '.nox', '.turbo', '.vite',
  'build', 'dist', 'coverage', 'htmlcov', 'test-results', 'playwright-report',
  '.gradle', '.kotlin', '.bootstrap', '.build', '.swiftpm', '.generated', 'xcuserdata',
]);
const ROOT_DATA = new Set([
  'omnivoice_data', 'data', 'local', 'results', 'download', 'engines', 'cudnn8_compat',
  '.tmp', 'wix-diagnostic-artifacts',
]);
const SKIP = new Set(['.git', '.agents', '.codex', '.claude', '.aws', 'research']);

/** Return default app-owned profiles; shared model caches and custom locations are excluded. */
export function appDataPaths({ home = homedir(), platform = process.platform, env = process.env } = {}) {
  const paths = [join(home, '.config', 'omnivoice')];
  if (platform === 'darwin') {
    for (const name of ['VoiceStudio', 'OmniVoice']) {
      for (const base of ['Application Support', 'Caches', 'Logs'])
        paths.push(join(home, 'Library', base, name));
    }
    paths.push(join(home, 'Library/Caches/com.voicestudio.desktop'));
    paths.push(join(home, 'Library/Preferences/com.voicestudio.desktop.plist'));
    paths.push(join(home, 'Library/Saved Application State/com.voicestudio.desktop.savedState'));
  } else if (platform === 'win32') {
    const roaming = env.APPDATA || join(home, 'AppData/Roaming');
    const local = env.LOCALAPPDATA || join(home, 'AppData/Local');
    paths.push(join(roaming, 'VoiceStudio'), join(roaming, 'OmniVoice'));
    paths.push(join(local, 'VoiceStudio'));
    // LOCALAPPDATA/OmniVoice/hf_cache is a shared Hugging Face cache, not an
    // app-owned profile. Preserve it just like ~/.cache/huggingface elsewhere.
  } else {
    paths.push(join(home, '.omnivoice'));
    for (const base of [env.XDG_CONFIG_HOME || join(home, '.config'),
      env.XDG_CACHE_HOME || join(home, '.cache'),
      env.XDG_DATA_HOME || join(home, '.local/share'),
      env.XDG_STATE_HOME || join(home, '.local/state')]) {
      paths.push(join(base, 'VoiceStudio'), join(base, 'voicestudio'));
    }
  }
  return [...new Set(paths.map((path) => resolve(path)))];
}

function stat(path) {
  try { return lstatSync(path); }
  catch (error) { if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null; throw error; }
}

function within(path, parent) {
  const rel = relative(parent, path);
  return !rel || (rel !== '..' && !rel.startsWith(`..${sep}`) && !isAbsolute(rel));
}

// Never traverse a link on the way to a deletion. A link at the leaf itself is
// safe: rm unlinks it without touching the destination. Canonicalize the home
// and checkout first so macOS's /var -> /private/var alias is harmless.
function checkParents(path) {
  for (let parent = dirname(path); parent !== dirname(parent); parent = dirname(parent)) {
    if (stat(parent)?.isSymbolicLink()) throw new Error(`Refusing symlink parent: ${parent}`);
  }
}

/** Inventory removable paths without mutations, preserving tracked files and repository boundaries. */
export function planCleanup({ root = ROOT, home = homedir(), platform = process.platform, env = process.env } = {}) {
  root = realpathSync(root);
  home = realpathSync(home);
  // Fail closed without Git; never infer that a source/fixture is disposable.
  const tracked = execFileSync('git', ['ls-files', '-z', '--cached'], {
    cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
  })
    .split('\0').filter(Boolean);
  const protectedPaths = new Set();
  for (const file of tracked) {
    let path = resolve(root, file);
    while (within(path, root)) {
      protectedPaths.add(path);
      if (path === root) break;
      path = dirname(path);
    }
  }
  const targets = [];
  const preservedTrees = new Map();
  function containsPreservedTree(directory) {
    if (!preservedTrees.has(directory)) {
      // A generated parent must not hide a nested checkout or agent config.
      // Never follow directory symlinks while looking for these boundaries.
      preservedTrees.set(directory, readdirSync(directory, { withFileTypes: true }).some(
        (entry) => SKIP.has(entry.name) ||
          (entry.isDirectory() && containsPreservedTree(join(directory, entry.name))),
      ));
    }
    return preservedTrees.get(directory);
  }
  function walk(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (SKIP.has(entry.name)) continue;
      const path = join(directory, entry.name);
      if (entry.isDirectory() && existsSync(join(path, '.git'))) continue;
      const rel = relative(root, path).split(sep).join('/');
      const directoryLike = entry.isDirectory() || entry.isSymbolicLink();
      const generated = directoryLike && (
        GENERATED.has(entry.name) || /^\.venv[-.]/.test(entry.name) ||
        (directory === root && (ROOT_DATA.has(entry.name) || /^\.(cache|tmp)-/.test(entry.name))) ||
        (rel.startsWith('native/') && /^target(?:$|[-.])/.test(entry.name)) ||
        (rel.startsWith('mobile/ios/') && /^\.build-/.test(entry.name)) ||
        rel === 'mobile/ios/VoiceStudio/VoiceStudio.xcodeproj' ||
        (directory === join(root, 'electron') && /^(out|release|\.tmp.*)$/.test(entry.name)) ||
        (entry.isDirectory() && existsSync(join(path, 'pyvenv.cfg')))
      );
      const artifact = !directoryLike && (
        /\.(py[co]|tsbuildinfo|log)$/.test(entry.name) ||
        /^\.coverage(?:\.|$)/.test(entry.name) || entry.name === '.DS_Store' || entry.name === 'Thumbs.db'
      );
      if ((generated || artifact) && !protectedPaths.has(path) &&
          (!entry.isDirectory() || !containsPreservedTree(path))) targets.push(path);
      else if (entry.isDirectory()) walk(path);
    }
  }
  walk(root);
  for (const path of appDataPaths({ home, platform, env })) {
    if (within(root, path) || within(path, root) || within(home, path))
      throw new Error(`Refusing app-data path overlapping the checkout or home: ${path}`);
    checkParents(path);
    if (stat(path)) targets.push(path);
  }
  return [...new Set(targets)].sort();
}

/** Preview or execute a validated cleanup plan; report partial failures with a nonzero CLI exit. */
export function cleanAll({ dryRun = false, log = console.log, ...options } = {}) {
  const targets = planCleanup(options);
  log(dryRun ? 'Cleanup preview (no files deleted):' : 'Deleting VoiceStudio environments, builds and app data:');
  let failures = 0;
  for (const path of targets) {
    log(path);
    if (dryRun) continue;
    try {
      checkParents(path);
      rmSync(path, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
    } catch (error) {
      failures++;
      log(`FAILED: ${error.message}`);
    }
  }
  if (failures) throw new Error(`${failures} cleanup target(s) failed; close the app/backend and retry.`);
  log(`${dryRun ? 'Would remove' : 'Removed'} ${targets.length} target(s).`);
  return targets;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    if (process.argv.slice(2).some((arg) => arg !== '--dry-run'))
      throw new Error('Usage: node scripts/clean-all.mjs [--dry-run]');
    cleanAll({ dryRun: process.argv.includes('--dry-run') });
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
