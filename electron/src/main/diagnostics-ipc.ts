import { ipcMain, type IpcMainInvokeEvent } from 'electron';
import { DIAGNOSTIC_CHANNELS } from '../preload/diagnostics';
import { validUiInput } from './diagnostics-schema';
import type { DiagnosticReports } from './diagnostics';

export function registerDiagnosticIpc(
  reports: DiagnosticReports,
  assertTrusted: (event: IpcMainInvokeEvent) => void,
  ready: Promise<void>,
  ipc: Pick<typeof ipcMain, 'handle' | 'removeHandler'> = ipcMain,
): () => void {
  ipc.handle(DIAGNOSTIC_CHANNELS.getStatus, async (event) => {
    assertTrusted(event);
    await ready;
    return reports.status();
  });
  ipc.handle(DIAGNOSTIC_CHANNELS.setConsent, async (event, enabled: unknown) => {
    assertTrusted(event);
    if (typeof enabled !== 'boolean') throw new Error('Invalid diagnostic consent');
    await ready;
    return reports.setConsent(enabled);
  });
  ipc.handle(DIAGNOSTIC_CHANNELS.capture, (event, input: unknown, expectedEpoch: unknown) => {
    assertTrusted(event);
    if (!validUiInput(input)) throw new Error('Invalid diagnostic report');
    if (typeof expectedEpoch !== 'string' || !/^[a-f0-9]{32}$/.test(expectedEpoch)) return;
    reports.capture({ ...input, source: 'renderer' }, expectedEpoch);
  });
  return () => Object.values(DIAGNOSTIC_CHANNELS).forEach((channel) => ipc.removeHandler(channel));
}
