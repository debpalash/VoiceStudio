// @vitest-environment node
import { afterAll, beforeAll, expect, it } from 'vitest';
import { build } from 'vite';
import { execFileSync } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

let directory: string;
beforeAll(async () => {
  directory = await mkdtemp(join(tmpdir(), 'voice-real-rejection-'));
  const entry = join(directory, 'entry.ts');
  const modulePath = (name: string) => JSON.stringify(resolve(__dirname, name));
  await writeFile(
    entry,
    [
      `export { DiagnosticReports } from ${modulePath('diagnostics.ts')};`,
      `export { installDiagnosticProcessCapture } from ${modulePath('diagnostics-capture.ts')};`,
      `export { runDiagnosticOperation } from ${modulePath('diagnostic-operation.ts')};`,
    ].join('\n'),
  );
  await build({
    configFile: false,
    logLevel: 'silent',
    build: {
      ssr: entry,
      outDir: directory,
      emptyOutDir: false,
      rollupOptions: { output: { entryFileNames: 'capture.mjs' } },
    },
  });
  await writeFile(
    join(directory, 'probe.mjs'),
    String.raw`
    import { DiagnosticReports, installDiagnosticProcessCapture, runDiagnosticOperation } from './capture.mjs';
    import { mkdtemp, rm } from 'node:fs/promises';
    import { join } from 'node:path';
    import { tmpdir } from 'node:os';
    const directory = await mkdtemp(join(tmpdir(), 'voice-rejection-data-'));
    const reports = new DiagnosticReports({
      directory, identity: { app_version: '1.0.0', build_commit: '0000000', platform: 'linux', arch: 'x64' },
      automatic: false, env: {}, destination: { site: 'fixture', host: 'https://fixture.invalid', appUrl: 'https://fixture.invalid' },
    });
    await reports.initialize();
    const stop = installDiagnosticProcessCapture(reports);
    const [control, mode] = process.argv[2].split(':');
    if (control !== 'off-to-on' && control !== 'unknown-off-to-on') await reports.setConsent(true);
    let release;
    const held = new Promise(resolve => { release = resolve; });
    const action = async () => {
      await held;
      // A nested Promise after the grant changes still belongs to the old unit.
      await Promise.resolve();
      const fail = () => { throw Object.assign(new Error('PRIVATE_TEXT'), { name: control === 'cancel' ? 'AbortError' : 'Error' }); };
      if (mode === 'detached') { void Promise.resolve().then(fail); return; }
      if (mode === 'timer') { setTimeout(fail, 0); return; }
      fail();
    };
    if (control.startsWith('unknown')) action();
    else runDiagnosticOperation(reports, action);
    if (control === 'off-to-on' || control === 'unknown-off-to-on') await reports.setConsent(true);
    if (control === 'a-to-b' || control === 'unknown-a-to-b') { await reports.setConsent(false); await reports.setConsent(true); }
    if (control === 'on-to-off') await reports.setConsent(false);
    // This fixture-only fatal listener lets the child inspect the monitor's
    // result. Production capture only observes and never changes fatal policy.
    const delivered = new Promise(resolve => process.once(mode === 'timer' ? 'uncaughtException' : 'unhandledRejection', () => setImmediate(resolve)));
    release(); await delivered;
    const pending = reports.status().pending_count;
    // A separate later action under the current grant is eligible.
    let freshPending = pending;
    if (['off-to-on', 'a-to-b', 'unknown-off-to-on', 'unknown-a-to-b'].includes(control)) {
      const next = new Promise(resolve => process.once('unhandledRejection', () => setImmediate(resolve)));
      runDiagnosticOperation(reports, async () => { throw new Error('PRIVATE_FRESH'); });
      await next; freshPending = reports.status().pending_count;
    }
    console.log(JSON.stringify({ pending, freshPending }));
    stop(); await reports.close(); await rm(directory, { recursive: true, force: true });
  `,
  );
});
afterAll(async () => {
  if (directory) await rm(directory, { recursive: true, force: true });
});

it.each(
  ['off-to-on', 'a-to-b', 'same-grant', 'unknown', 'unknown-off-to-on', 'unknown-a-to-b', 'cancel', 'on-to-off'].flatMap((control) => [
    control,
    `${control}:detached`,
    `${control}:timer`,
  ]),
)('uses the originating operation for actual Node rejection and timer failures: %s', (control) => {
  const kind = control.split(':')[0];
  const captured = kind === 'same-grant';
  const output = execFileSync(process.execPath, [join(directory, 'probe.mjs'), control], {
    encoding: 'utf8',
    timeout: 15_000,
  });
  expect(JSON.parse(output)).toEqual({
    pending: captured ? 1 : 0,
    freshPending: captured || ['off-to-on', 'a-to-b', 'unknown-off-to-on', 'unknown-a-to-b'].includes(kind) ? 1 : 0,
  });
  expect(output).not.toContain('PRIVATE_');
});
