// @vitest-environment node
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it } from 'vitest';

it('registers every Python runtime IPC handler exactly once', () => {
  const source = readFileSync(resolve(__dirname, 'ipc.ts'), 'utf8');
  for (const name of [
    'backendGetPythonRuntime',
    'backendChoosePythonRuntime',
    'backendUseManagedPython',
    'backendDiscoverPythonRuntimes',
    'backendUseDiscoveredPythonRuntime',
    'backendConfirmPythonRuntime',
    'backendPlanPythonRepair',
    'backendInstallPythonRepair',
  ]) {
    expect(source.match(new RegExp(`ipcMain\\.handle\\(CHANNELS\\.${name}\\b`, 'g')) ?? [], name).toHaveLength(1);
  }
});

it('keeps confirmation and repair side effects behind trusted staged tokens', () => {
  const source = readFileSync(resolve(__dirname, 'ipc.ts'), 'utf8');
  const confirm = source.slice(
    source.indexOf('ipcMain.handle(CHANNELS.backendConfirmPythonRuntime'),
    source.indexOf('ipcMain.handle(CHANNELS.backendPlanPythonRepair'),
  );
  const repair = source.slice(
    source.indexOf('ipcMain.handle(CHANNELS.backendInstallPythonRepair'),
    source.indexOf('ipcMain.handle(CHANNELS.backendHostSnapshot'),
  );

  expect(confirm).toContain('assertTrustedMainFrame(event, owner)');
  expect(confirm).toContain('const staged = consumeStage(raw, event)');
  expect(confirm.indexOf('const staged = consumeStage(raw, event)')).toBeLessThan(confirm.indexOf('savePythonRuntimeSelection'));
  expect(confirm.indexOf('savePythonRuntimeSelection')).toBeLessThan(confirm.indexOf('await supervisor.restart()'));

  expect(repair).toContain('assertTrustedMainFrame(event, owner)');
  expect(repair).toContain('dialog.showMessageBox(owner');
  expect(repair).toContain("buttons: ['Install', 'Cancel']");
  expect(repair).toContain("['-m', 'pip', 'install'");
  expect(repair).toContain('staged.plan.packages');
});
