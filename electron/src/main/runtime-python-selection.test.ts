// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { SELECTED_RUNTIME_IMPORT_PROBE, buildPythonRepairPlan, isVenvInterpreter, createOpaqueToken, sanitizedPythonEnvironment } from './runtime-python-selection';

const userData = mkdtempSync(join(tmpdir(), 'voicestudio-runtime-test-'));
vi.mock('electron', () => ({ app: { getPath: () => userData } }));

import { shouldBypassManagedRuntime, validatePythonRuntime, clearPythonRuntimeSelection, loadPythonRuntimeSelection, savePythonRuntimeSelection } from './runtime-python-selection';

describe('existing Python runtime selection', () => {
  beforeEach(() => clearPythonRuntimeSelection());
  afterEach(() => clearPythonRuntimeSelection());

  it('persists and reloads an absolute interpreter path', () => {
    savePythonRuntimeSelection('/tmp/fixture/.venv/bin/python');
    expect(loadPythonRuntimeSelection()).toEqual({ pythonPath: '/tmp/fixture/.venv/bin/python' });
  });

  it('rejects a missing interpreter without spawning it', async () => {
    const result = await validatePythonRuntime('/tmp/does-not-exist/python', process.cwd());
    expect(result).toMatchObject({ ok: false, detail: 'interpreter_not_found' });
  });

  it('bypasses managed readiness only for a packaged selected interpreter', () => {
    expect(shouldBypassManagedRuntime(true, '/tmp/.venv/bin/python', undefined)).toBe(true);
    expect(shouldBypassManagedRuntime(false, '/tmp/.venv/bin/python', undefined)).toBe(false);
    expect(shouldBypassManagedRuntime(true, '/tmp/.venv/bin/python', 'custom-backend')).toBe(false);
  });
  it('verifies the backend entry module, not only a dependency allowlist', () => {
    expect(SELECTED_RUNTIME_IMPORT_PROBE).toContain("print('VOICESTUDIO_BACKEND_IMPORT=main'");
    expect(SELECTED_RUNTIME_IMPORT_PROBE).toContain('import main');
    expect(SELECTED_RUNTIME_IMPORT_PROBE).toContain('sys.version_info[:2] < (3, 11)');
    expect(SELECTED_RUNTIME_IMPORT_PROBE).not.toContain('sys.version_info[:2] < [3,11]');
  });

  it('builds repair plans only from reviewed project requirements', () => {
    const project = mkdtempSync(join(tmpdir(), 'voicestudio-manifest-'));
    writeFileSync(join(project, 'pyproject.toml'), '[project]\ndependencies = [\n  "torch>=2.4",\n  "torchaudio>=2.4",\n  "setuptools>=75,<80",\n]\n');
    expect(buildPythonRepairPlan('/tmp/requirements/project', ['torch'], project)).toEqual({
      interpreter: '/tmp/requirements/project', packages: ['torch>=2.4'],
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
