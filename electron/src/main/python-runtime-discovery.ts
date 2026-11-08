import * as nativeFs from 'node:fs/promises';
import { homedir } from 'node:os';
import * as paths from 'node:path';

export interface PythonCandidate { pythonPath: string; source: string; version?: string }
export interface PythonDiscoveryWarning { path: string; reason: 'permission_denied' | 'scan_limit' | 'read_error' }
export interface PythonDiscoveryResult { candidates: PythonCandidate[]; warnings: PythonDiscoveryWarning[]; partial: boolean; scannedRoots: string[] }
export interface PythonDiscoveryFileSystem {
  readdir(path: string): Promise<string[]>;
  stat(path: string): Promise<{ isFile(): boolean; isDirectory(): boolean }>;
  lstat(path: string): Promise<{ isSymbolicLink(): boolean; isDirectory(): boolean }>;
  realpath?(path: string): Promise<string>;
  readFile(path: string): Promise<string>;
}
export interface PythonDiscoveryOptions {
  fs?: Partial<PythonDiscoveryFileSystem>; env?: Record<string, string | undefined>;
  homeDir?: string; cwd?: string; platform?: 'darwin' | 'linux' | 'win32';
  pathSeparator?: string; roots?: string[]; maxDirs?: number; maxDepth?: number; timeoutMs?: number;
}
class ScanLimit extends Error { constructor(readonly path: string) { super('scan_limit'); } }
const SKIP = new Set(['node_modules', '.git', 'site-packages', '__pycache__', '.cache', '.cargo', '.rustup', 'target', 'dist', 'release', '.next', 'build']);
const PYTHON_NAME = /^python(?:\d+(?:\.\d+)*t?)?(?:\.exe)?$/i;

/** Explicit, read-only common-location discovery. Finding a path is NOT validation. */
export async function discoverPythonRuntimes(options: PythonDiscoveryOptions = {}): Promise<PythonDiscoveryResult> {
  const fs: PythonDiscoveryFileSystem = {
    readdir: async p => nativeFs.readdir(p), stat: nativeFs.stat, lstat: nativeFs.lstat,
    realpath: nativeFs.realpath, readFile: p => nativeFs.readFile(p, 'utf8'), ...options.fs,
  };
  const windows = (options.platform ?? process.platform) === 'win32';
  const path = windows ? paths.win32 : paths.posix;
  const env = options.env ?? process.env;
  const home = path.resolve(options.homeDir ?? env.HOME ?? env.USERPROFILE ?? homedir());
  const cwd = options.cwd ?? process.cwd();
  const deadline = Date.now() + Math.max(1, options.timeoutMs ?? 4000);
  const maxDirs = Math.max(1, options.maxDirs ?? 4000), maxDepth = Math.max(0, options.maxDepth ?? 4);
  const result: PythonDiscoveryResult = { candidates: [], warnings: [], partial: false, scannedRoots: [] };
  const identities = new Set<string>(), visited = new Set<string>();
  let dirs = 0;
  const key = (p: string) => windows ? p.toLowerCase() : p;
  const warn = (p: string, reason: PythonDiscoveryWarning['reason']) => {
    result.partial = true;
    if (result.warnings.length < 100 && !result.warnings.some(w => w.path === p && w.reason === reason)) result.warnings.push({ path: p, reason });
  };
  async function read<T>(p: string, fn: () => Promise<T>): Promise<T | undefined> {
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new ScanLimit(p);
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([fn(), new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new ScanLimit(p)), remaining); })]);
    } catch (error) {
      if (error instanceof ScanLimit) throw error;
      const code = (error as NodeJS.ErrnoException).code;
      if (code !== 'ENOENT' && code !== 'ENOTDIR') warn(p, code === 'EACCES' || code === 'EPERM' ? 'permission_denied' : 'read_error');
      return undefined;
    } finally { if (timer) clearTimeout(timer); }
  }
  const canonical = async (p: string) => key(path.normalize(fs.realpath ? await read(p, () => fs.realpath!(p)) ?? p : p));
  const source = (p: string) => {
    const s = p.replaceAll('\\', '/').toLowerCase();
    return s.includes('/.pyenv/versions/') ? 'pyenv' : /\/(?:\.?conda|miniconda\d*|anaconda\d*)(?:\/|$)/.test(s) ? 'conda' : s.includes('/uv/python/') ? 'uv' : 'venv';
  };
  async function candidate(p: string, from: string) {
    const info = await read(p, () => fs.stat(p));
    if (!info?.isFile()) return;
    const root = path.dirname(path.dirname(p));
    const cfg = await read(path.join(root, 'pyvenv.cfg'), () => fs.readFile(path.join(root, 'pyvenv.cfg')));
    const conda = await read(path.join(root, 'conda-meta'), () => fs.stat(path.join(root, 'conda-meta')));
    const isEnvironment = cfg !== undefined || conda?.isDirectory() || ['venv', 'pyenv', 'conda', 'uv'].includes(from);
    const identity = isEnvironment ? 'env:' + await canonical(root) : 'file:' + await canonical(p);
    if (identities.has(identity)) return;
    identities.add(identity);
    const version = cfg?.match(/^version\s*=\s*([^\r\n]+)$/im)?.[1]?.trim();
    result.candidates.push({ pythonPath: path.normalize(p), source: from, ...(version ? { version } : {}) });
    if (result.candidates.length >= 256) throw new ScanLimit(p);
  }
  const commonNames = windows ? ['python.exe', 'python3.exe'] : ['python', 'python3'];
  async function bin(p: string, from: string) {
    for (const name of commonNames) await candidate(path.join(p, name), from);
    // Include versioned installations without assuming a particular Python release.
    const names = await read(p, () => fs.readdir(p));
    for (const name of (names ?? []).filter(n => PYTHON_NAME.test(n) && !commonNames.includes(n)).sort()) await candidate(path.join(p, name), from);
  }
  async function environment(p: string, from: string) {
    // A venv symlink is safe to inspect directly; its directory is never traversed.
    await bin(path.join(p, windows ? 'Scripts' : 'bin'), from);
    if (windows) await candidate(path.join(p, 'python.exe'), from); // Conda on Windows.
  }
  try {
    for (const p of (env.PATH ?? env.Path ?? '').split(options.pathSeparator ?? (windows ? ';' : ':')).filter(Boolean)) {
      // Relative PATH entries are deliberately ignored, as their identity changes with cwd.
      if (path.isAbsolute(p)) await bin(path.normalize(p), 'path');
    }
    const systems = windows ? [path.join(env.LOCALAPPDATA ?? path.join(home, 'AppData', 'Local'), 'Programs', 'Python')] : ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/opt/local/bin'];
    if (!options.roots && !windows) for (const p of systems) {
      result.scannedRoots.push(p); await bin(p, 'system');
    }
    const defaults = [
      path.join(home, '.pyenv', 'versions'), path.join(home, '.conda', 'envs'),
      path.join(home, 'miniconda3'), path.join(home, 'anaconda3'),
      path.join(home, '.local', 'share', 'uv', 'python'),
      ...['Work', 'Tools', 'Projects', 'Developer'].map(p => path.join(home, p)), path.join(home, 'gopath', 'src'),
      ...(windows ? [...systems, path.join(home, 'source', 'repos')] : ['/opt/miniconda3', '/opt/anaconda3', '/Library/Frameworks/Python.framework/Versions']),
    ];
    for (const raw of options.roots ?? defaults) {
      const root = path.isAbsolute(raw) ? path.normalize(raw) : path.resolve(cwd, raw);
      result.scannedRoots.push(root);
      const queue = [{ p: root, depth: 0 }];
      for (let i = 0; i < queue.length; i++) {
        const { p, depth } = queue[i];
        if (dirs >= maxDirs) throw new ScanLimit(p);
        const info = await read(p, () => fs.lstat(p));
        if (!info?.isDirectory() || info.isSymbolicLink()) continue;
        const id = await canonical(p);
        if (visited.has(id)) continue;
        visited.add(id); dirs++;
        await environment(p, source(p));
        const names = await read(p, () => fs.readdir(p));
        for (const name of names ?? []) {
          const child = path.join(p, name), lower = name.toLowerCase();
          if (SKIP.has(lower)) continue;
          if (lower === '.venv' || lower === 'venv') { await environment(child, 'venv'); continue; }
          if (lower === 'bin' || lower === 'scripts' || lower === 'lib' || lower === 'lib64' || lower === 'include' || lower === 'conda-meta') continue;
          const entry = await read(child, () => fs.lstat(child));
          if (!entry?.isDirectory() || entry.isSymbolicLink()) continue;
          if (depth >= maxDepth) { warn(child, 'scan_limit'); continue; }
          queue.push({ p: child, depth: depth + 1 });
        }
      }
    }
  } catch (error) {
    if (!(error instanceof ScanLimit)) throw error;
    warn(error.path, 'scan_limit');
  }
  return result;
}
