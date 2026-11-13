import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { test } from 'node:test';

const require = createRequire(import.meta.url);
const builderRequire = createRequire(require.resolve('electron-builder/package.json'));
const template = readFileSync(resolve(dirname(builderRequire.resolve('app-builder-lib/package.json')),
  'templates/nsis/include/allowOnlyOneInstallerInstance.nsh'), 'utf8');
const predicates = [...template.matchAll(/\| \? \{([^}]+)\}/g)].map((match) => match[1]);
assert.equal(predicates.length, 2, 'installer detection and termination both have a process filter');
assert.equal(predicates[0], predicates[1], 'detection and termination must target the same processes');
assert.match(template, /System::Call 'kernel32::SetEnvironmentVariableW\(w "ELECTRON_BUILDER_INSTALL_ROOT", w "\$INSTDIR"\)i\.r0'/,
  'installer passes the unquoted path through its child environment');
const powershell = process.platform === 'win32' ? 'powershell.exe' : 'pwsh';
const available = spawnSync(powershell, ['-NoProfile', '-Command', 'exit 0']);
const probes = [...template.matchAll(/nsExec::Exec `"\$PowerShellPath" (.+)`/g)]
  .map((match) => match[1]);

test('installer-owned PowerShell probes skip profiles and interactive prompts', () => {
  assert.equal(probes.length, 4);
  for (const probe of probes) {
    assert.match(probe, /-NoProfile\b/i);
    assert.match(probe, /-NonInteractive\b/i);
  }
});

test('the NSIS availability probe does not load a user profile', {
  // PowerShell on macOS/Linux exposes an isolated profile through XDG_CONFIG_HOME.
  // Windows profile locations cannot be redirected without changing real user state.
  skip: process.platform === 'win32' || available.error?.code === 'ENOENT',
  timeout: 60_000,
}, () => {
  const scratch = mkdtempSync(join(tmpdir(), 'voice-nsis-profile-'));
  const loaded = join(scratch, 'profile-loaded');
  const env = { ...process.env, XDG_CONFIG_HOME: scratch, VS_PROFILE_MARKER: loaded };
  try {
    const lookup = spawnSync(powershell, ['-NoProfile', '-NonInteractive', '-Command', '$PROFILE'], {
      encoding: 'utf8', env, timeout: 20_000,
    });
    assert.equal(lookup.status, 0, lookup.stderr);
    const profile = lookup.stdout.trim();
    assert.ok(profile.startsWith(scratch), 'only a scratch PowerShell profile may be written');
    mkdirSync(dirname(profile), { recursive: true });
    writeFileSync(profile, 'Set-Content -LiteralPath $env:VS_PROFILE_MARKER -Value loaded\n', 'utf8');

    // Execute the availability command shipped in the actual NSIS template.
    const marker = probes[0].indexOf('-C "');
    assert.ok(marker >= 0);
    const switches = probes[0].slice(0, marker).trim().split(/\s+/).filter(Boolean);
    // CIM is Windows-only. Supply the command locally so Get-Command does not
    // search installed modules on macOS/Linux; the installed probe stays intact.
    const command = `function Get-CimInstance { }\n${probes[0].slice(marker + 4, -1)}`;
    const control = spawnSync(powershell, [...switches.filter((flag) => !/^-NoProfile$/i.test(flag)), '-Command', command], {
      encoding: 'utf8', env, timeout: 20_000,
    });
    assert.ifError(control.error);
    assert.equal(control.status, 0, control.stderr);
    assert.ok(existsSync(loaded), 'negative control must load the isolated profile');
    rmSync(loaded);
    const result = spawnSync(powershell, [...switches, '-Command', command], {
      encoding: 'utf8', env, timeout: 20_000,
    });
    assert.ifError(result.error);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(loaded), false, 'installer probe must skip the isolated profile');
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
});

for (const root of ['E:\\NN\\VoiceStudio', 'E:\\NN\\VoiceStudio\\', "E:\\O'Brien\\VoiceStudio", 'E:\\']) {
  test(`installer only matches processes inside ${root}`, {
    skip: process.platform !== 'win32' && available.error?.code === 'ENOENT',
  }, () => {
    const clean = root.replace(/\\+$/, '');
    const processes = [
      { ProcessId: 1, ExecutablePath: `${clean}\\VoiceStudio.exe` },
      { ProcessId: 2, ExecutablePath: `${clean.toLowerCase()}\\resources\\python.exe` },
      { ProcessId: 3, ExecutablePath: `${clean}-old\\VoiceStudio.exe` },
      { ProcessId: 4, ExecutablePath: `${clean}-0.5.7-setup.exe` },
      { ProcessId: 4242, ExecutablePath: `${clean}\\setup.exe` },
      { ProcessId: 5, ExecutablePath: null },
    ];
    const predicate = predicates[0].replaceAll('$$', '$').replaceAll('$INSTDIR', root).replaceAll('$pid', '4242');
    const script = `$items = ConvertFrom-Json $env:VS_PROCESS_FIXTURES; ConvertTo-Json -InputObject @($items | Where-Object { ${predicate} } | ForEach-Object { $_.ProcessId }) -Compress`;
    const result = spawnSync(powershell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], {
      encoding: 'utf8',
      env: { ...process.env, ELECTRON_BUILDER_INSTALL_ROOT: root, VS_PROCESS_FIXTURES: JSON.stringify(processes) },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout.trim()), [1, 2]);
  });
}

test('installer detects a real Win32_Process child', { skip: process.platform !== 'win32', timeout: 40_000 }, async () => {
  const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
  try {
    await once(child, 'spawn');
    const predicate = predicates[0].replaceAll('$$', '$');
    const script = `$target = Get-CimInstance -ClassName Win32_Process -Filter 'ProcessId = ${child.pid}'; ConvertTo-Json -InputObject @($target | Where-Object { ${predicate} } | ForEach-Object { $_.ProcessId }) -Compress`;
    const result = spawnSync(powershell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], {
      encoding: 'utf8',
      timeout: 30_000,
      env: { ...process.env, ELECTRON_BUILDER_INSTALL_ROOT: dirname(process.execPath) },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout.trim()), [child.pid]);
  } finally {
    if (child.pid && child.exitCode === null && child.signalCode === null) {
      const exited = once(child, 'exit');
      child.kill();
      await exited;
    }
  }
});
