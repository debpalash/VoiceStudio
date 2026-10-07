import { registerSiteBrowser } from './site-browser';
import { portableFilename } from './portable-filename';
import { saveFiltersFor } from './save-filters';
import { resolveBackendDownloadUrl } from './backend-download';
import {
  assertMediaTool,
  authorizeMediaTool,
  authorizeModelsDirectory,
} from './media-authorization';
import { isTrustedRenderer } from './trusted-renderer';
import { replaceFile } from './replace-file';
import { encodeDownloadFailure, MAX_FAILURE_BODY_CHARS } from '../shared/download-failure';
import { homedir, tmpdir } from 'node:os';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  net,
  shell,
  systemPreferences,
  type IpcMainInvokeEvent,
} from 'electron';
import type { BackendSupervisor } from './backend';
import { extractWordScript } from './script-import';
import type {
  BackendStatus,
  NativePermissions,
  NativePermissionStatus,
  SaveAudioRequest,
  SaveAudioResult,
  SaveDataRequest,
} from '../preload/index.d';
import {
  DISK_RESET_SCOPES,
  purgeResetScopes,
  scanResetScopes,
  type ResetRoots,
} from './reset-data';
import { inspectDataRelocation, relocateDataDirectory } from './data-relocation';
import { createUninstallPlan, scanUninstallTargets, type UninstallRoots } from './uninstall-data';
import { scheduleUninstallCleanup } from './uninstall-cleanup';
import { sendToLiveWindow } from './window-safety';
import { activateProLicense, deactivateProLicense, proLicenseStatus } from './pro-license';

export const CHANNELS = {
  proStatus: 'pro:status',
  proActivate: 'pro:activate',
  proDeactivate: 'pro:deactivate',
  permissionsGetState: 'permissions:getState',
  permissionsOpenSettings: 'permissions:openSettings',
  filesAuthorizeMediaTool: 'files:authorizeMediaTool',
  filesAuthorizeModelsDirectory: 'files:authorizeModelsDirectory',
  filesExtractScript: 'files:extractScript',
  backendGetStatus: 'backend:getStatus',
  backendAcknowledgeCrash: 'backend:acknowledgeCrash',
  backendRestart: 'backend:restart',
  backendSetupRuntime: 'backend:setupRuntime',
  backendCleanSetupRuntime: 'backend:cleanSetupRuntime',
  backendSetRuntimeRegion: 'backend:setRuntimeRegion',
  backendChooseRuntimeLocation: 'backend:chooseRuntimeLocation',
  backendUseDefaultRuntimeLocation: 'backend:useDefaultRuntimeLocation',
  backendGetConnection: 'backend:getConnection',
  backendTestRemote: 'backend:testRemote',
  backendUseRemote: 'backend:useRemote',
  backendUseLocal: 'backend:useLocal',
  backendWebsocketUrl: 'backend:websocketUrl',
  backendStatus: 'backend:status',
  maintenanceScanReset: 'maintenance:scanReset',
  maintenancePurgeReset: 'maintenance:purgeReset',
  maintenanceChooseDataDirectory: 'maintenance:chooseDataDirectory',
  maintenanceRelocateDataDirectory: 'maintenance:relocateDataDirectory',
  maintenanceRelocationProgress: 'maintenance:relocationProgress',
  maintenanceScanUninstall: 'maintenance:scanUninstall',
  maintenancePurgeUninstall: 'maintenance:purgeUninstall',
  filesSaveAudio: 'files:saveAudio',
  filesSaveData: 'files:saveData',
  filesRevealPath: 'files:revealPath',
  filesOpenExternal: 'files:openExternal',
  windowMinimize: 'window:minimize',
  windowToggleMaximize: 'window:toggleMaximize',
  windowClose: 'window:close',
  windowIsMaximized: 'window:isMaximized',
  windowMaximized: 'window:maximized',
} as const;

function microphonePermission(): NativePermissionStatus {
  if (process.platform === 'linux') return 'unknown';
  const status = systemPreferences.getMediaAccessStatus('microphone');
  if (status === 'granted' || status === 'denied') return status;
  if (status === 'not-determined') return 'prompt';
  return 'unknown';
}

function nativePermissions(): NativePermissions {
  return {
    platform: process.platform,
    microphone: microphonePermission(),
    ...(process.platform === 'darwin'
      ? {
          accessibility: systemPreferences.isTrustedAccessibilityClient(false)
            ? ('granted' as const)
            : ('denied' as const),
        }
      : {}),
  };
}

async function openPermissionSettings(kind: unknown): Promise<boolean> {
  if (kind !== 'microphone' && kind !== 'accessibility') {
    throw new Error('Invalid permission settings request');
  }
  let target = '';
  if (process.platform === 'darwin') {
    target = `x-apple.systempreferences:com.apple.preference.security?Privacy_${
      kind === 'microphone' ? 'Microphone' : 'Accessibility'
    }`;
  } else if (process.platform === 'win32' && kind === 'microphone') {
    target = 'ms-settings:privacy-microphone';
  }
  if (!target) return false;
  await shell.openExternal(target);
  return true;
}

const RELOCATION_SELECTION_TTL_MS = 10 * 60 * 1000;

const OPEN_EXTERNAL_PROTOCOLS = new Set(['http:', 'https:', 'file:', 'mailto:']);

function windowFor(event: IpcMainInvokeEvent | Electron.IpcMainEvent): BrowserWindow | null {
  return BrowserWindow.fromWebContents(event.sender);
}

function broadcast(channel: string, payload: unknown): void {
  for (const win of BrowserWindow.getAllWindows()) {
    sendToLiveWindow(win, channel, payload);
  }
}

function assertString(value: unknown, what: string): string {
  if (typeof value !== 'string' || value.length === 0) throw new Error(`Invalid ${what}`);
  return value;
}

function assertSaveAudioRequest(value: unknown): SaveAudioRequest {
  if (typeof value !== 'object' || value === null) throw new Error('Invalid save request');
  const req = value as Partial<SaveAudioRequest>;
  if (req.method !== undefined && req.method !== 'GET' && req.method !== 'POST') {
    throw new Error('Invalid download method');
  }
  return {
    url: assertString(req.url, 'audio url'),
    suggestedName: portableFilename(assertString(req.suggestedName, 'file name')),
    method: req.method,
  };
}

function assertSaveDataRequest(value: unknown): SaveDataRequest {
  if (typeof value !== 'object' || value === null) throw new Error('Invalid save request');
  const req = value as Partial<SaveDataRequest>;
  if (!(req.data instanceof Uint8Array)) throw new Error('Invalid file data');
  return {
    data: req.data,
    suggestedName: portableFilename(assertString(req.suggestedName, 'file name')),
  };
}

function assertTrustedMainFrame(
  event: IpcMainInvokeEvent,
  owner: BrowserWindow | null,
): asserts owner is BrowserWindow {
  if (
    !owner ||
    event.sender !== owner.webContents ||
    event.senderFrame !== owner.webContents.mainFrame ||
    !isTrustedRenderer(event.senderFrame.url, process.env.ELECTRON_RENDERER_URL)
  ) {
    throw new Error('Untrusted main-frame request');
  }
}

/** Read-only connection metadata is also needed by trusted utility windows. */
function assertTrustedTopLevelFrame(event: IpcMainInvokeEvent): void {
  const owner = windowFor(event);
  if (
    !owner ||
    event.sender !== owner.webContents ||
    event.senderFrame !== owner.webContents.mainFrame ||
    !isTrustedRenderer(event.senderFrame.url, process.env.ELECTRON_RENDERER_URL)
  ) {
    throw new Error('Untrusted top-level request');
  }
}

function remoteInput(raw: unknown): { url: string; apiKey: string } {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw))
    throw new Error('Invalid remote backend request');
  const input = raw as { url?: unknown; apiKey?: unknown };
  if (
    typeof input.url !== 'string' ||
    input.url.length > 2048 ||
    typeof input.apiKey !== 'string' ||
    input.apiKey.length > 8192
  ) {
    throw new Error('Invalid remote backend request');
  }
  return { url: input.url, apiKey: input.apiKey };
}

async function backendDataDirectory(supervisor: BackendSupervisor): Promise<string> {
  const response = await fetch(`${supervisor.baseUrl}/system/info`, {
    headers: supervisor.requestHeaders(),
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error('Backend unavailable');
  const info = (await response.json()) as {
    app_version?: unknown;
    data_dir?: unknown;
  };
  if (
    typeof info.app_version !== 'string' ||
    typeof info.data_dir !== 'string' ||
    !isAbsolute(info.data_dir)
  ) {
    throw new Error('Backend did not advertise its data directory');
  }
  return info.data_dir;
}

function samePath(left: string, right: string): boolean {
  const a = resolve(left);
  const b = resolve(right);
  return process.platform === 'win32'
    ? a.toLocaleLowerCase('en-US') === b.toLocaleLowerCase('en-US')
    : a === b;
}

async function waitForBackendDataDirectory(
  supervisor: BackendSupervisor,
  expected: string,
  timeoutMs = 30_000,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if (samePath(await backendDataDirectory(supervisor), expected)) return true;
    } catch {
      // Startup probes fail until the backend binds; keep waiting within the deadline.
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return false;
}

async function resetRoots(supervisor: BackendSupervisor): Promise<ResetRoots> {
  if (supervisor.connection.remote)
    throw new Error('Reset is available for the local backend only');
  const headers = supervisor.requestHeaders();
  const [infoResponse, modelsResponse] = await Promise.all([
    fetch(`${supervisor.baseUrl}/system/info`, {
      headers,
      signal: AbortSignal.timeout(5000),
    }),
    fetch(`${supervisor.baseUrl}/api/settings/storage/models-dir`, {
      headers,
      signal: AbortSignal.timeout(5000),
    }),
  ]);
  if (!infoResponse.ok || !modelsResponse.ok)
    throw new Error('Backend storage state is unavailable');
  const info = (await infoResponse.json()) as {
    app_version?: unknown;
    data_dir?: unknown;
    crash_log_path?: unknown;
  };
  const models = (await modelsResponse.json()) as {
    configured?: unknown;
    effective?: unknown;
    default?: unknown;
  };
  const data = info.data_dir;
  const modelPath = models.configured || models.effective || models.default;
  if (
    typeof info.app_version !== 'string' ||
    typeof data !== 'string' ||
    !isAbsolute(data) ||
    typeof modelPath !== 'string' ||
    !isAbsolute(modelPath)
  ) {
    throw new Error('Backend returned invalid storage paths');
  }
  const crash = info.crash_log_path;
  return {
    data,
    models: modelPath,
    logs: typeof crash === 'string' && isAbsolute(crash) ? dirname(crash) : null,
    temp: tmpdir(),
  };
}

async function uninstallRoots(supervisor: BackendSupervisor): Promise<UninstallRoots> {
  const roots = await resetRoots(supervisor);
  return {
    data: roots.data,
    environment: app.getPath('userData'),
    runtimeEnvironment: supervisor.ownedRuntimeRoot,
    logs: app.getPath('logs'),
    userEnvironment: join(homedir(), '.config', 'omnivoice'),
    models: roots.models,
  };
}

/** The failed response's status and body, encoded so the renderer can show
 *  the backend's own `detail` instead of a bare status code (#2616). */
async function downloadFailureMessage(res: Response): Promise<string> {
  let body = '';
  try {
    body = (await res.text()).slice(0, MAX_FAILURE_BODY_CHARS);
  } catch {
    // Unreadable body: the status alone still goes through.
  }
  return encodeDownloadFailure({ status: res.status, statusText: res.statusText, body });
}

export function registerIpc(
  supervisor: BackendSupervisor,
  getMainWindow: () => BrowserWindow | null,
  exitForUninstall: () => Promise<void> = async () => app.exit(0),
): void {
  registerSiteBrowser(getMainWindow);
  let maintenanceBusy = false;
  let relocationSelection: {
    authorization: string;
    source: string;
    target: string;
    createdAt: number;
  } | null = null;
  supervisor.subscribe((status: BackendStatus) => broadcast(CHANNELS.backendStatus, status));

  ipcMain.handle(CHANNELS.proStatus, (event) => {
    assertTrustedMainFrame(event, getMainWindow());
    return proLicenseStatus();
  });
  ipcMain.handle(CHANNELS.proActivate, (event, key: unknown) => {
    assertTrustedMainFrame(event, getMainWindow());
    return activateProLicense(key);
  });
  ipcMain.handle(CHANNELS.proDeactivate, (event) => {
    assertTrustedMainFrame(event, getMainWindow());
    return deactivateProLicense();
  });

  ipcMain.handle(CHANNELS.permissionsGetState, (event) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    return nativePermissions();
  });
  ipcMain.handle(CHANNELS.permissionsOpenSettings, (event, kind: unknown) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    return openPermissionSettings(kind);
  });

  ipcMain.handle(CHANNELS.backendGetStatus, (): BackendStatus => supervisor.status);
  ipcMain.handle(CHANNELS.backendAcknowledgeCrash, (event) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    return supervisor.acknowledgeLastCrash();
  });
  ipcMain.handle(CHANNELS.backendGetConnection, () => supervisor.connection);
  ipcMain.handle(CHANNELS.backendTestRemote, async (event, raw: unknown) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    const input = remoteInput(raw);
    return supervisor.testRemote(input.url, input.apiKey);
  });
  ipcMain.handle(CHANNELS.backendUseRemote, async (event, raw: unknown) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    const input = remoteInput(raw);
    return supervisor.useRemote(input.url, input.apiKey);
  });
  ipcMain.handle(CHANNELS.backendUseLocal, async (event) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    return supervisor.useLocal();
  });
  ipcMain.handle(CHANNELS.backendWebsocketUrl, async (event, raw: unknown) => {
    assertTrustedTopLevelFrame(event);
    if (raw !== '/ws/transcribe' && raw !== '/ws/events' && raw !== '/ws/tts')
      throw new Error('Unsupported backend WebSocket path');
    return supervisor.websocketUrl(raw);
  });
  ipcMain.handle(CHANNELS.maintenanceScanReset, async (event) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    return scanResetScopes(await resetRoots(supervisor));
  });
  ipcMain.handle(CHANNELS.maintenancePurgeReset, async (event, raw: unknown) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    if (
      !Array.isArray(raw) ||
      raw.length === 0 ||
      raw.length > DISK_RESET_SCOPES.length ||
      raw.some((scope) => typeof scope !== 'string' || !DISK_RESET_SCOPES.includes(scope as never))
    ) {
      throw new Error('Invalid reset scope selection');
    }
    if (maintenanceBusy) throw new Error('Reset is already running');
    maintenanceBusy = true;
    let stopped = false;
    try {
      const roots = await resetRoots(supervisor);
      await supervisor.shutdown();
      stopped = true;
      await new Promise((resolve) => setTimeout(resolve, 600));
      const report = await purgeResetScopes(roots, raw, homedir());
      report.restarted = true;
      return report;
    } finally {
      if (stopped) await supervisor.start();
      maintenanceBusy = false;
    }
  });
  ipcMain.handle(CHANNELS.maintenanceChooseDataDirectory, async (event) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    if (supervisor.connection.remote) throw new Error('local_only');
    if (!supervisor.status.managed) throw new Error('backend_not_managed');
    const source = await backendDataDirectory(supervisor);
    const picked = await dialog.showOpenDialog(owner, {
      defaultPath: dirname(source),
      properties: ['openDirectory', 'createDirectory'],
    });
    if (picked.canceled || !picked.filePaths[0]) return null;
    const plan = await inspectDataRelocation(source, picked.filePaths[0]);
    relocationSelection = {
      authorization: randomUUID(),
      source: plan.source,
      target: plan.target,
      createdAt: Date.now(),
    };
    return { ...plan, authorization: relocationSelection.authorization };
  });
  ipcMain.handle(CHANNELS.maintenanceRelocateDataDirectory, async (event, raw: unknown) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    if (
      typeof raw !== 'string' ||
      !relocationSelection ||
      raw !== relocationSelection.authorization
    ) {
      throw new Error('invalid_authorization');
    }
    const selected = relocationSelection;
    relocationSelection = null;
    if (Date.now() - selected.createdAt > RELOCATION_SELECTION_TTL_MS) {
      throw new Error('authorization_expired');
    }
    if (maintenanceBusy) throw new Error('maintenance_busy');
    if (supervisor.connection.remote) throw new Error('local_only');
    if (!supervisor.status.managed) throw new Error('backend_not_managed');
    if (!samePath(await backendDataDirectory(supervisor), selected.source)) {
      throw new Error('source_changed');
    }
    const progress = (stage: string) =>
      sendToLiveWindow(owner, CHANNELS.maintenanceRelocationProgress, { stage });
    maintenanceBusy = true;
    try {
      return await relocateDataDirectory(selected.source, selected.target, {
        stop: () => supervisor.shutdown(),
        start: () => supervisor.start(),
        verify: (target) => waitForBackendDataDirectory(supervisor, target),
        progress,
      });
    } finally {
      maintenanceBusy = false;
    }
  });
  ipcMain.handle(CHANNELS.maintenanceScanUninstall, async (event) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    if (supervisor.connection.remote) throw new Error('local_only');
    return scanUninstallTargets(await uninstallRoots(supervisor));
  });
  ipcMain.handle(CHANNELS.maintenancePurgeUninstall, async (event, includeModels: unknown) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    if (typeof includeModels !== 'boolean') throw new Error('invalid_uninstall_request');
    if (maintenanceBusy) throw new Error('maintenance_busy');
    if (supervisor.connection.remote) throw new Error('local_only');
    if (!supervisor.status.managed) throw new Error('backend_not_managed');
    maintenanceBusy = true;
    let stopped = false;
    try {
      const targets = await scanUninstallTargets(await uninstallRoots(supervisor));
      const plan = await createUninstallPlan(targets, includeModels, homedir());
      if (plan.refused.length) throw new Error('unsafe_uninstall_target');
      await supervisor.shutdown();
      stopped = true;
      await new Promise((resolve) => setTimeout(resolve, 600));
      await scheduleUninstallCleanup(plan.paths);
      await exitForUninstall();
      return { scheduled: true, size_bytes: plan.size_bytes };
    } catch (error) {
      if (stopped) await supervisor.start().catch(() => undefined);
      maintenanceBusy = false;
      throw error;
    }
  });
  ipcMain.handle(CHANNELS.backendSetupRuntime, (event) => {
    const owner = getMainWindow();
    if (
      !owner ||
      event.sender !== owner.webContents ||
      event.senderFrame !== owner.webContents.mainFrame ||
      !isTrustedRenderer(event.senderFrame.url, process.env.ELECTRON_RENDERER_URL)
    )
      throw new Error('Untrusted runtime setup request');
    return supervisor.setupRuntime();
  });
  ipcMain.handle(CHANNELS.backendCleanSetupRuntime, (event) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    return supervisor.cleanSetupRuntime();
  });
  ipcMain.handle(CHANNELS.backendSetRuntimeRegion, (event, raw: unknown) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    return supervisor.setRuntimeRegion(raw);
  });
  ipcMain.handle(CHANNELS.backendChooseRuntimeLocation, async (event, rawTitle: unknown) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    const title = typeof rawTitle === 'string' && rawTitle.length <= 200 ? rawTitle : undefined;
    const picked = await dialog.showOpenDialog(owner, {
      ...(title ? { title } : {}),
      properties: ['openDirectory', 'createDirectory'],
    });
    if (picked.canceled || !picked.filePaths[0]) return null;
    return supervisor.setRuntimeLocation(picked.filePaths[0]);
  });
  ipcMain.handle(CHANNELS.backendUseDefaultRuntimeLocation, (event) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    return supervisor.setRuntimeLocation(null);
  });
  ipcMain.handle(CHANNELS.backendRestart, () => supervisor.restart());
  ipcMain.handle(CHANNELS.filesExtractScript, (_event, input: unknown) => extractWordScript(input));

  ipcMain.handle(CHANNELS.filesAuthorizeMediaTool, async (event, raw: unknown) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    const kind = assertMediaTool(raw);
    const picked = await dialog.showOpenDialog(owner, {
      properties: ['openFile'],
    });
    if (picked.canceled || !picked.filePaths[0]) return null;
    return authorizeMediaTool(await backendDataDirectory(supervisor), picked.filePaths[0], kind);
  });

  ipcMain.handle(CHANNELS.filesAuthorizeModelsDirectory, async (event, raw: unknown = false) => {
    const owner = getMainWindow();
    assertTrustedMainFrame(event, owner);
    if (typeof raw !== 'boolean') throw new Error('Invalid models-directory request');
    let selected = '';
    if (!raw) {
      const picked = await dialog.showOpenDialog(owner, {
        properties: ['openDirectory', 'createDirectory'],
      });
      if (picked.canceled || !picked.filePaths[0]) return null;
      selected = picked.filePaths[0];
    }
    return authorizeModelsDirectory(await backendDataDirectory(supervisor), selected);
  });

  ipcMain.handle(CHANNELS.filesSaveAudio, async (event, raw: unknown): Promise<SaveAudioResult> => {
    const owner = windowFor(event) ?? getMainWindow();
    assertTrustedMainFrame(event, owner);
    const req = assertSaveAudioRequest(raw);
    const source = resolveBackendDownloadUrl(req.url, supervisor.baseUrl);
    const options: Electron.SaveDialogOptions = {
      defaultPath: req.suggestedName,
      filters: saveFiltersFor(req.suggestedName),
    };
    const picked = owner
      ? await dialog.showSaveDialog(owner, options)
      : await dialog.showSaveDialog(options);
    if (picked.canceled || !picked.filePath) return { canceled: true };
    const res = await net.fetch(source, {
      method: req.method ?? 'GET',
      headers: supervisor.requestHeaders(),
      bypassCustomProtocolHandlers: true,
    });
    if (!res.ok) throw new Error(await downloadFailureMessage(res));
    await replaceFile(picked.filePath, Buffer.from(await res.arrayBuffer()));
    return { canceled: false, path: picked.filePath };
  });

  ipcMain.handle(CHANNELS.filesSaveData, async (event, raw: unknown): Promise<SaveAudioResult> => {
    const owner = windowFor(event) ?? getMainWindow();
    assertTrustedMainFrame(event, owner);
    const req = assertSaveDataRequest(raw);
    const picked = await dialog.showSaveDialog(owner, {
      defaultPath: req.suggestedName,
      filters: saveFiltersFor(req.suggestedName),
    });
    if (picked.canceled || !picked.filePath) return { canceled: true };
    await replaceFile(picked.filePath, req.data);
    return { canceled: false, path: picked.filePath };
  });

  ipcMain.handle(CHANNELS.filesRevealPath, (_event, raw: unknown) => {
    const path = assertString(raw, 'path');
    if (!isAbsolute(path)) throw new Error('revealPath needs an absolute path');
    shell.showItemInFolder(path);
  });

  ipcMain.handle(CHANNELS.filesOpenExternal, async (_event, raw: unknown) => {
    const target = assertString(raw, 'url');
    if (isAbsolute(target)) {
      const failure = await shell.openPath(target);
      if (failure) throw new Error(failure);
      return;
    }
    const url = new URL(target);
    if (!OPEN_EXTERNAL_PROTOCOLS.has(url.protocol)) {
      throw new Error(`Refusing to open ${url.protocol} URLs`);
    }
    await shell.openExternal(url.toString());
  });

  ipcMain.on(CHANNELS.windowMinimize, (event) => {
    (windowFor(event) ?? getMainWindow())?.minimize();
  });
  ipcMain.on(CHANNELS.windowToggleMaximize, (event) => {
    const win = windowFor(event) ?? getMainWindow();
    if (!win) return;
    if (win.isMaximized()) win.unmaximize();
    else win.maximize();
  });
  ipcMain.on(CHANNELS.windowClose, (event) => {
    (windowFor(event) ?? getMainWindow())?.close();
  });
  ipcMain.handle(CHANNELS.windowIsMaximized, (event) => {
    return (windowFor(event) ?? getMainWindow())?.isMaximized() ?? false;
  });
}

/** Push `window:maximized` to the window renderer after maximize state changes. */
export function wireWindowMaximizeEvents(win: BrowserWindow): void {
  const send = (maximized: boolean) => sendToLiveWindow(win, CHANNELS.windowMaximized, maximized);
  win.on('maximize', () => send(true));
  win.on('unmaximize', () => send(false));
}
