import { spawn } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startHealthBackend } from './fake-health-backend.mjs';
import { listSmokes } from './smoke-inventory.mjs';

/**
 * Runs every offline renderer smoke against an already-running `smoke:server`
 * (see the "Electron renderer workflow smokes" step in .github/workflows/ci.yml).
 *
 * Usage: node electron/tests/run-renderer-smokes.mjs [name-substring ...]
 */
const dir = dirname(fileURLToPath(import.meta.url));

/** Extra environment a smoke needs beyond the shared CI environment; keep each entry commented. */
export const SMOKE_ENV = {};

function runSmoke(file) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [join(dir, file)], {
      env: { ...process.env, ...SMOKE_ENV[file] },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '';
    child.stdout.on('data', (chunk) => (output += chunk));
    child.stderr.on('data', (chunk) => (output += chunk));
    const timer = setTimeout(() => child.kill('SIGKILL'), 120_000);
    child.once('close', (status) => {
      clearTimeout(timer);
      resolve({ status, stdout: output, stderr: '' });
    });
  });
}

const filters = process.argv.slice(2);
const smokes = listSmokes(dir)
  .filter((smoke) => smoke.kind === 'renderer')
  .filter((smoke) => !filters.length || filters.some((filter) => smoke.file.includes(filter)));

// The smoke server proxies /api to OMNIVOICE_PORT. Smokes mock the routes they care about; the
// rest used to hang on a connecting screen with nothing listening, so answer them locally.
const backendPort = Number(process.env.OMNIVOICE_PORT) || 0;
const backend = backendPort ? await startHealthBackend({ port: backendPort }) : null;

const results = [];
for (const { file } of smokes) {
  const started = Date.now();
  // Async spawn: the fake backend lives in this process and must keep serving while a smoke runs.
  const run = await runSmoke(file);
  const ok = run.status === 0;
  results.push({ file, ok, ms: Date.now() - started });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${String(Date.now() - started).padStart(6)}ms  ${file}`);
  if (!ok) {
    const output = `${run.stdout ?? ''}${run.stderr ?? ''}`.trim().split('\n').slice(-12);
    console.log(output.map((line) => `      ${line}`).join('\n'));
  }
}

await backend?.close();
const failed = results.filter((result) => !result.ok);
console.log(`\n${results.length - failed.length}/${results.length} renderer smokes passed`);
process.exit(failed.length ? 1 : 0);
