import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
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
