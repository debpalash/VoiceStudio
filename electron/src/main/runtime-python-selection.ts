import { app } from 'electron';
import { existsSync, readFileSync, statSync, writeFileSync, mkdirSync, rmSync, mkdtempSync } from 'node:fs';
import { dirname, isAbsolute, resolve, join } from 'node:path';
import { tmpdir } from 'node:os';
import { randomBytes } from 'node:crypto';
import { runRuntimeProbe } from './runtime-probe';
import { RUNTIME_IMPORT_PROBE, lastImportAttempted } from './runtime-import-probe';

const FILE = 'python-runtime-selection.json';
const MIN_PYTHON = [3, 11] as const;
const PROBE_TIMEOUT_MS = 180_000;
const VERSION_PREFIX = 'VOICESTUDIO_PYTHON=';

export interface PythonRuntimeSelection { pythonPath: string; }
export interface PythonRuntimeValidation {
  ok: boolean;
  pythonPath: string;
  version?: string;
  missingModules?: string[];
  unsupportedModules?: string[];
  detail?: string;
  repairPlan?: PythonRepairPlan;
}
export interface PythonRepairPlan { interpreter: string; packages: string[]; }

/** Import names which differ from the package names in the reviewed manifest. */
const IMPORT_PACKAGE_ALIAS: Record<string, string> = {
  faster_whisper: 'faster-whisper',
  pkg_resources: 'setuptools',
  PIL: 'pillow',
};

export function createOpaqueToken(): string { return randomBytes(32).toString('base64url'); }

/** Only package specs reviewed in the repository may be emitted for repair. */
export function buildPythonRepairPlan(interpreter: string, missingModules: string[], cwd = process.cwd()): PythonRepairPlan | null {
  let source: string;
  try { source = readFileSync(join(cwd, 'pyproject.toml'), 'utf8'); } catch { return null; }
  const dependencyBlock = source.match(/^dependencies\s*=\s*\[(.*?)^\s*\]/ms)?.[1];
  if (!dependencyBlock) return null;
  const reviewed = new Map<string, string>();
  for (const match of dependencyBlock.matchAll(/^[ \t]*["']([A-Za-z0-9_.-]+(?:\s*(?:[<>=!~].*|@\s*https?:\/\/.*))?)["'][,]?/gm)) {
    const spec = match[1].trim();
    const name = spec.split(/[<>=!~@\s]/, 1)[0].toLowerCase().replaceAll('_', '-');
    reviewed.set(name, spec);
  }
  const packages: string[] = [];
  for (const module of [...new Set(missingModules)]) {
    const root = module.split('.')[0];
    const packageName = (IMPORT_PACKAGE_ALIAS[root] ?? root).toLowerCase().replaceAll('_', '-');
    const spec = reviewed.get(packageName);
    if (!spec) return null;
    packages.push(spec);
  }
  return packages.length ? { interpreter: resolve(interpreter), packages: [...new Set(packages)].sort() } : null;
}

/** Environment for probes and pip: no user-controlled Python or pip config. */
export function sanitizedPythonEnvironment(extra: NodeJS.ProcessEnv = {}): NodeJS.ProcessEnv {
  const env = { ...process.env, ...extra };
  for (const key of ['PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONINSPECT']) delete env[key];
  for (const key of Object.keys(env)) if (key.startsWith('PIP_')) delete env[key];
  env.PYTHONDONTWRITEBYTECODE = '1';
  env.PYTHONNOUSERSITE = '1';
  env.PIP_CONFIG_FILE = process.platform === 'win32' ? 'NUL' : '/dev/null';
  return env;
}

export function isVenvInterpreter(pythonPath: string, _cwd = process.cwd()): boolean {
  const normalized = resolve(pythonPath);
  if (/^(?:\/usr\/|\/System\/|C:\\Windows\\|C:\\Program Files\\)/i.test(normalized)) return false;
  const bin = dirname(normalized);
  const root = dirname(bin);
  try { return existsSync(join(root, 'pyvenv.cfg')); } catch { return false; }
}

function selectionFile(): string { return join(app.getPath('userData'), FILE); }

export function loadPythonRuntimeSelection(): PythonRuntimeSelection | null {
  try {
    const parsed = JSON.parse(readFileSync(selectionFile(), 'utf8')) as { pythonPath?: unknown };
    if (typeof parsed.pythonPath !== 'string' || !isAbsolute(parsed.pythonPath)) return null;
    return { pythonPath: resolve(parsed.pythonPath) };
  } catch { return null; }
}

export function savePythonRuntimeSelection(pythonPath: string): PythonRuntimeSelection {
  const normalized = resolve(pythonPath);
  mkdirSync(dirname(selectionFile()), { recursive: true });
  writeFileSync(selectionFile(), JSON.stringify({ pythonPath: normalized }, null, 2), 'utf8');
  return { pythonPath: normalized };
}

export function clearPythonRuntimeSelection(): void { rmSync(selectionFile(), { force: true }); }

export function selectedPythonPath(): string | null { return loadPythonRuntimeSelection()?.pythonPath ?? null; }

/** A saved interpreter must skip packaged managed-runtime readiness checks. */
export function shouldBypassManagedRuntime(
  packaged: boolean,
  selected: string | null,
  override: string | undefined,
): boolean {
  return packaged && Boolean(selected) && !override?.trim();
}

export const BACKEND_IMPORT_MARKER = 'VOICESTUDIO_BACKEND_IMPORT=main';
export const SELECTED_RUNTIME_IMPORT_PROBE = [
  'import json, os, sys',
  `sys.path.insert(0, os.path.join(os.getcwd(), 'backend'))`,
  `print(${JSON.stringify(VERSION_PREFIX)} + sys.version.split()[0], flush=True)`,
  `print('VOICESTUDIO_VENV=' + ('1' if sys.prefix != sys.base_prefix else '0'), flush=True)`,
  `print('VOICESTUDIO_EXTERNALLY_MANAGED=' + ('1' if sys.prefix == sys.base_prefix and __import__('os').path.exists(__import__('sysconfig').get_path('stdlib') + '/EXTERNALLY-MANAGED') else '0'), flush=True)`,
  `if sys.version_info[:2] < (${MIN_PYTHON[0]}, ${MIN_PYTHON[1]}): raise SystemExit('python_version_unsupported')`,
  RUNTIME_IMPORT_PROBE,
  `print('${BACKEND_IMPORT_MARKER}', flush=True)`,
  'import main',
].join('\n');

export async function validatePythonRuntime(pythonPath: string, cwd: string): Promise<PythonRuntimeValidation> {
  const normalized = resolve(pythonPath);
  if (!isAbsolute(pythonPath) || !existsSync(normalized))
    return { ok: false, pythonPath: normalized, detail: 'interpreter_not_found' };
  try {
    if (!statSync(normalized).isFile()) return { ok: false, pythonPath: normalized, detail: 'interpreter_not_file' };
  } catch { return { ok: false, pythonPath: normalized, detail: 'interpreter_not_readable' }; }
  const sandbox = mkdtempSync(join(tmpdir(), 'voicestudio-runtime-probe-'));
  try {
    const output = await runRuntimeProbe(normalized, ['-c', SELECTED_RUNTIME_IMPORT_PROBE], cwd, {
      ...sanitizedPythonEnvironment(),
      PYTHONDONTWRITEBYTECODE: '1',
      PYTHONNOUSERSITE: '1',
      HF_HUB_OFFLINE: '1',
      TRANSFORMERS_OFFLINE: '1',
      OMNIVOICE_DATA_DIR: join(sandbox, 'data'),
      OMNIVOICE_CACHE_DIR: join(sandbox, 'models'),
      HF_HOME: join(sandbox, 'models'),
      HF_HUB_CACHE: join(sandbox, 'models'),
      TORCH_HOME: join(sandbox, 'models'),
      OMNIVOICE_DISABLE_FILE_LOG: '1',
      TMPDIR: join(sandbox, 'tmp'),
      TMP: join(sandbox, 'tmp'),
      TEMP: join(sandbox, 'tmp'),
    }, { timeoutMs: PROBE_TIMEOUT_MS, captureStderr: true });
    const version = output.split(/\r?\n/).find((line) => line.startsWith(VERSION_PREFIX))?.slice(VERSION_PREFIX.length);
    if (!version) return { ok: false, pythonPath: normalized, detail: 'version_unavailable' };
    const [major, minor] = version.split('.').map(Number);
    if (major < MIN_PYTHON[0] || (major === MIN_PYTHON[0] && minor < MIN_PYTHON[1]))
      return { ok: false, pythonPath: normalized, version, detail: 'python_version_unsupported' };
    if (!isVenvInterpreter(normalized, cwd) || !output.includes('VOICESTUDIO_VENV=1'))
      return { ok: false, pythonPath: normalized, version, detail: 'venv_required' };
    if (output.includes('VOICESTUDIO_EXTERNALLY_MANAGED=1'))
      return { ok: false, pythonPath: normalized, version, detail: 'externally_managed' };
    return { ok: true, pythonPath: normalized, version };
  } catch (error) {
    const failure = error as { stdout?: unknown; stderr?: unknown; message?: unknown };
    const text = `${String(failure.stdout ?? '')}\n${String(failure.stderr ?? '')}`;
    const version = text.match(/VOICESTUDIO_PYTHON=([0-9]+\.[0-9]+(?:\.[0-9]+)?)/)?.[1];
    const attempted = text.includes(BACKEND_IMPORT_MARKER)
      ? 'main'
      : lastImportAttempted(text);
    const missing: string[] = [...text.matchAll(/No module named ['\"]([^'\"]+)['\"]/g)].map((m) => m[1]);
    const detail = version && (() => {
      const [major, minor] = version.split('.').map(Number);
      return major < MIN_PYTHON[0] || (major === MIN_PYTHON[0] && minor < MIN_PYTHON[1]);
    })()
      ? 'python_version_unsupported'
      : attempted
        ? `missing_or_failed:${attempted}`
        : String(failure.message ?? 'probe_failed').split('\n')[0];
    const uniqueMissing = [...new Set(missing)];
    const canRepair = isVenvInterpreter(normalized, cwd) && text.includes('VOICESTUDIO_VENV=1') && !text.includes('VOICESTUDIO_EXTERNALLY_MANAGED=1');
    const repairPlan = canRepair && uniqueMissing.length ? buildPythonRepairPlan(normalized, uniqueMissing, cwd) : undefined;
    return {
      ok: false, pythonPath: normalized, version,
      missingModules: uniqueMissing, detail,
      ...(uniqueMissing.length && !repairPlan ? { unsupportedModules: uniqueMissing } : {}),
      ...(repairPlan ? { repairPlan } : {}),
    };
  } finally {
    rmSync(sandbox, { recursive: true, force: true });
  }
}
