// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import { SELECTED_RUNTIME_IMPORT_PROBE, STARTUP_IMPORT_PROBE, buildPythonRepairPlan, isVenvInterpreter, createOpaqueToken, sanitizedPythonEnvironment } from './runtime-python-selection';

const userData = mkdtempSync(join(tmpdir(), 'voicestudio-runtime-test-'));
vi.mock('electron', () => ({ app: { getPath: () => userData } }));

import { needsManagedRuntime, validatePythonRuntime, clearPythonRuntimeSelection, loadPythonRuntimeSelection, savePythonRuntimeSelection } from './runtime-python-selection';

describe('existing Python runtime selection', () => {
  beforeEach(() => clearPythonRuntimeSelection());
  afterEach(() => clearPythonRuntimeSelection());

  it('persists and reloads an absolute interpreter path', () => {
    // Saved paths are resolved, so Windows hosts store a drive-qualified path.
    const python = resolve('/tmp/fixture/.venv/bin/python');
    savePythonRuntimeSelection(python);
    expect(loadPythonRuntimeSelection()).toEqual({ pythonPath: python });
  });

  it('rejects a missing interpreter without spawning it', async () => {
    const result = await validatePythonRuntime('/tmp/does-not-exist/python', process.cwd());
    expect(result).toMatchObject({ ok: false, detail: 'interpreter_not_found' });
  });

  it('runs managed readiness only for a packaged, setup-capable, unselected, unoverridden launch', () => {
    expect(needsManagedRuntime(true, true, null, false)).toBe(true);
    // Source builds use the checkout's .venv; setup cannot run there.
    expect(needsManagedRuntime(false, true, null, false)).toBe(false);
    expect(needsManagedRuntime(false, true, '/tmp/.venv/bin/python', false)).toBe(false);
    // OMNIVOICE_BACKEND_CMD brings its own interpreter.
    expect(needsManagedRuntime(true, true, null, true)).toBe(false);
    // Store builds run the bundled runtime.
    expect(needsManagedRuntime(true, false, null, false)).toBe(false);
    expect(needsManagedRuntime(true, true, '/tmp/.venv/bin/python', false)).toBe(false);
  });
  it('verifies the backend entry module, not only a dependency allowlist', () => {
    expect(SELECTED_RUNTIME_IMPORT_PROBE).toContain("print('VOICESTUDIO_BACKEND_IMPORT=main'");
    expect(SELECTED_RUNTIME_IMPORT_PROBE).toContain('import main');
    // The deferred router fan-out is probed too, after main (#3701 review).
    expect(SELECTED_RUNTIME_IMPORT_PROBE.indexOf(STARTUP_IMPORT_PROBE)).toBeGreaterThan(
      SELECTED_RUNTIME_IMPORT_PROBE.indexOf('import main'),
    );
    expect(SELECTED_RUNTIME_IMPORT_PROBE).toContain('sys.version_info[:2] < (3, 11)');
    expect(SELECTED_RUNTIME_IMPORT_PROBE).not.toContain('sys.version_info[:2] < [3,11]');
  });

  /** A stand-in interpreter that prints the given probe output and exits. */
  function fakePython(lines: string[], exitCode = 0): string {
    const dir = mkdtempSync(join(tmpdir(), 'voicestudio-fake-python-'));
    const path = join(dir, 'python');
    writeFileSync(path, `#!/bin/sh\n${lines.map((l) => `echo '${l}'`).join('\n')}\nexit ${exitCode}\n`);
    chmodSync(path, 0o755);
    return path;
  }

  it.skipIf(process.platform === 'win32')('accepts a compatible non-venv (Conda/pyenv/system) interpreter', async () => {
    const python = fakePython([
      'VOICESTUDIO_PYTHON=3.11.9', 'VOICESTUDIO_VENV=0', 'VOICESTUDIO_EXTERNALLY_MANAGED=1',
      'VOICESTUDIO_BACKEND_IMPORT=main', 'VOICESTUDIO_STARTUP_IMPORTS=3',
    ]);
    await expect(validatePythonRuntime(python, process.cwd())).resolves.toMatchObject({ ok: true, version: '3.11.9' });
  });

  it.skipIf(process.platform === 'win32')('names the deferred startup import that failed after main imported', async () => {
    const python = fakePython([
      'VOICESTUDIO_PYTHON=3.11.9', 'VOICESTUDIO_VENV=1', 'VOICESTUDIO_EXTERNALLY_MANAGED=0',
      'VOICESTUDIO_IMPORT=torch', 'VOICESTUDIO_BACKEND_IMPORT=main', 'VOICESTUDIO_STARTUP_IMPORTS=2',
      'VOICESTUDIO_IMPORT=api.routers', 'VOICESTUDIO_IMPORT=api.routers.dub_core',
    ], 1);
    const result = await validatePythonRuntime(python, process.cwd());
    expect(result).toMatchObject({ ok: false, detail: 'missing_or_failed:api.routers.dub_core' });
  });

  const hostPython = [process.env.VOICESTUDIO_TEST_PYTHON, '/usr/bin/python3', 'python3', 'python']
    .filter((p): p is string => Boolean(p))
    .find((p) => {
      if (isAbsolute(p) && !existsSync(p)) return false;
      try { execFileSync(p, ['-c', 'import ast'], { stdio: 'ignore' }); return true; } catch { return false; }
    });

  it.skipIf(!hostPython)('imports the unguarded _phase_a_build_inner imports from main.py', () => {
    const root = mkdtempSync(join(tmpdir(), 'voicestudio-startup-probe-'));
    const routers = join(root, 'backend', 'api', 'routers');
    mkdirSync(routers, { recursive: true });
    writeFileSync(join(root, 'backend', 'api', '__init__.py'), '');
    writeFileSync(join(routers, '__init__.py'), '');
    writeFileSync(join(routers, 'good.py'), '');
    writeFileSync(join(routers, 'broken.py'), "raise RuntimeError('Form data requires python-multipart')\n");
    const main = (body: string) => writeFileSync(join(root, 'backend', 'main.py'), `def _phase_a_build_inner():\n${body}`);
    const run = () => execFileSync(hostPython!, ['-c', `import sys, os\nsys.path.insert(0, os.path.join(os.getcwd(), 'backend'))\n${STARTUP_IMPORT_PROBE}`], { cwd: root, encoding: 'utf8', stdio: 'pipe' });
    // Guarded (try/except) imports are optional at startup and are not probed.
    main("    try:\n        from api.routers import broken\n    except Exception:\n        pass\n    from api.routers import (good,)\n");
    expect(run()).toContain('VOICESTUDIO_STARTUP_IMPORTS=1');
    main('    from api.routers import (\n        good,\n        broken,\n    )\n');
    let output = '';
    try { run(); } catch (error) { output = String((error as { stdout?: unknown }).stdout ?? ''); }
    expect(output.trim().split('\n').at(-1)).toBe('VOICESTUDIO_IMPORT=api.routers.broken');
    // A main.py without the build function must fail, never pass silently.
    main('    pass\n');
    writeFileSync(join(root, 'backend', 'main.py'), 'x = 1\n');
    expect(run).toThrow();
  });

  it('builds repair plans only from reviewed project requirements', () => {
    const project = mkdtempSync(join(tmpdir(), 'voicestudio-manifest-'));
    writeFileSync(join(project, 'pyproject.toml'), '[project]\ndependencies = [\n  "torch>=2.4",\n  "torchaudio>=2.4",\n  "setuptools>=75,<80",\n]\n');
    expect(buildPythonRepairPlan('/tmp/requirements/project', ['torch'], project)).toEqual({
      interpreter: resolve('/tmp/requirements/project'), packages: ['torch>=2.4'],
    });
    expect(buildPythonRepairPlan('/tmp/requirements/project', ['torch', 'NoSuchModule'], project)).toBeNull();
    expect(buildPythonRepairPlan('/tmp/requirements/project', ['torchaudio'], project)?.packages).toEqual(['torchaudio>=2.4']);
  });

  it('does not inherit Python or pip installation overrides', () => {
    const env = sanitizedPythonEnvironment({ PYTHONPATH: '/tmp/evil', PIP_TARGET: '/tmp/evil', PIP_PREFIX: '/tmp/evil', PIP_USER: '1', PIP_INDEX_URL: 'https://evil.invalid' });
    expect(env.PYTHONPATH).toBeUndefined();
    expect(env.PIP_TARGET).toBeUndefined();
    expect(env.PIP_PREFIX).toBeUndefined();
    expect(env.PIP_USER).toBeUndefined();
    expect(env.PIP_CONFIG_FILE).toBeTruthy();
  });

  it('rejects system interpreters as repair targets', () => {
    expect(isVenvInterpreter('/usr/bin/python3', '/tmp/project')).toBe(false);
  });

  it('creates unguessable opaque tokens', () => {
    const token = createOpaqueToken();
    expect(token).toMatch(/^[A-Za-z0-9_-]{32,}$/);
    expect(createOpaqueToken()).not.toBe(token);
  });

  it('accepts the isolated verification interpreter when requested', async () => {
    const python = process.env.VOICESTUDIO_REAL_TEST_PYTHON;
    if (!python) return;
    const result = await validatePythonRuntime(python, process.cwd().endsWith('/electron') ? resolve(process.cwd(), '..') : process.cwd());
    expect(result).toMatchObject({ ok: true, pythonPath: python });
    expect(result.version).toMatch(/^\d+\.\d+(?:\.\d+)?$/);
  }, 180_000);

  it('runs a real fixture interpreter and reports missing imports', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'voicestudio-python-fixture-'));
    const fixture = join(dir, 'python');
    const realPython = process.env.VOICESTUDIO_TEST_PYTHON || '/usr/bin/python3';
    writeFileSync(fixture, `#!${realPython}\n`);
    chmodSync(fixture, 0o755);
    const result = await validatePythonRuntime(fixture, process.cwd());
    expect(result.ok).toBe(false);
    expect(result.detail || result.missingModules?.length || result.version).toBeTruthy();
  }, 30_000);
});
