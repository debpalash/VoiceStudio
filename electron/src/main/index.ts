import { createTrayIcon } from './tray-icon';
import { MAC_TRAFFIC_LIGHTS } from '../shared/utils/windowChrome';
import { installWatchFolders } from './watch-folders';
import { installNativeCapture } from './native-capture';
import { isTrustedRenderer } from './trusted-renderer';
import { dirname, join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { app, BrowserWindow, ipcMain, Menu, nativeImage, session, shell, Tray } from 'electron';
import { electronApp, is, optimizer } from '@electron-toolkit/utils';
import { BackendSupervisor, backendRoot } from './backend';
import { assertTrustedMainFrame, registerIpc, wireWindowMaximizeEvents } from './ipc';
import { APP_ORIGIN, installAppProtocol, registerAppScheme } from './protocol';
import { electronProxyPort, startDevBackendProxy } from './dev-backend-proxy';
import { DesktopUpdater, registerUpdateIpc } from './updater';
import { registerRepairAgents } from './repair-agents';
import { installRendererPermissions } from './media-permissions';
import { installBlankWindowGuard } from './blank-window-guard';
import { shouldOpenDevTools } from './devtools-policy';
import {
  installMainProcessErrorHandlers,
  MainErrorJournal,
  observeMainProcessTask,
} from './main-error-journal';
import { activateLiveWindow, isLiveWindow, sendToLiveWindow } from './window-safety';
import { startAccount } from './account';
import { installAppIdentity, installMacApplicationMenu } from './app-identity';
import { APP_ID, PRODUCT_NAME } from '../../../config/app-config.mjs';
import { restoreSecurityScopedDirectories } from './security-scoped-files';
import { homedir } from 'node:os';
import {
  addTerminalCommandToPath,
  setUpTerminalCommand,
  terminalCommandStatus,
  type TerminalCommandOptions,
} from './terminal-command';
import {
  clearDiscovery,
  IdleShutdown,
  idleShutdownMs,
  parseLaunchIntent,
  relaunchArguments,
  writeDiscovery,
  type BackendActivity,
  type LaunchIntent,
} from './headless';

// Packaged GUI launches can inherit a short-lived terminal pipe. When that
// launcher exits, diagnostic console writes emit EPIPE asynchronously and can
// otherwise take down the healthy Electron main process. In-app log rings keep
// the same backend events, so a closed stdout/stderr sink is safe to ignore.
process.stdout?.on('error', () => {});
process.stderr?.on('error', () => {});

const here = dirname(fileURLToPath(import.meta.url));
const PRELOAD_PATH = join(here, '../preload/index.mjs');
const RENDERER_DIR = join(here, '../renderer');

// Neutral startup surface; transparent caption controls follow the CSS title bar.
const BACKGROUND = '#0a0a0a';
const OVERLAY = { color: '#00000000', symbolColor: '#737373', height: 52 };

installAppIdentity(app);
registerAppScheme();

const mainErrors = new MainErrorJournal(
  join(app.getPath('userData'), 'main-process-errors.json'),
  __APP_VERSION__,
);
installMainProcessErrorHandlers(mainErrors);

let mainWindow: BrowserWindow | null = null;
let closeWatch: (() => void) | null = null;
let supervisor: BackendSupervisor | null = null;
let quitting = false;
let tray: Tray | null = null;
let closeCapture: (() => void) | null = null;
let closeDevProxy: (() => Promise<void>) | null = null;
let closeUpdates: (() => void) | null = null;
let closeRepairAgents: (() => void) | null = null;
let closeAccount: (() => void) | null = null;
let closeDirectoryScopes: (() => void) | null = null;
let quitPreparation: Promise<void> | null = null;

// A terminal command starts the app without a window (`--voicestudio-background`); the
// window then opens on demand in this same process (see ./headless.ts).
const launchIntent = parseLaunchIntent(process.argv);
const headlessOwner = launchIntent.headless && !launchIntent.checkUpdates;
let idleShutdown: IdleShutdown | null = null;
let relaunchIntent: LaunchIntent | null = null;
let checkForUpdates: (() => void) | null = null;
let publishedDiscovery = '';
const DISCOVERY_DIR = app.getPath('userData');
// Records that first-launch terminal setup ran (see setUpTerminalCommand).
const TERMINAL_COMMAND_STATE = 'terminal-command.json';

const PERSISTENCE_FLUSH_REQUEST = 'app:flush-persistence';
const PERSISTENCE_FLUSHED = 'app:persistence-flushed';

async function flushRendererPersistence(): Promise<void> {
  const owner = mainWindow;
  if (!owner || owner.isDestroyed() || owner.webContents.isDestroyed()) return;
  const token = randomUUID();
  await new Promise<void>((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      ipcMain.removeListener(PERSISTENCE_FLUSHED, acknowledge);
      resolve();
    };
    const acknowledge = (event: Electron.IpcMainEvent, received: unknown) => {
      if (event.sender === owner.webContents && received === token) finish();
    };
    const timeout = setTimeout(finish, 2500);
    ipcMain.on(PERSISTENCE_FLUSHED, acknowledge);
    try {
      owner.webContents.send(PERSISTENCE_FLUSH_REQUEST, token);
    } catch {
      finish();
    }
  });
}

/** Tell terminal commands where this app's backend listens. */
function publishDiscovery(): void {
  const backend = supervisor;
  if (!backend || quitPreparation) return;
  const record = {
    schema: 1 as const,
    stage: backend.status.stage,
    url: backend.baseUrl,
    pid: process.pid,
    version: __APP_VERSION__,
    headless: headlessOwner,
    window: isLiveWindow(mainWindow),
    remote: backend.connection.remote,
  };
  const serialized = JSON.stringify(record);
  if (serialized === publishedDiscovery) return;
  try {
    writeDiscovery(DISCOVERY_DIR, record);
    publishedDiscovery = serialized;
  } catch (error) {
    console.warn('[headless] Could not publish the backend address:', error);
  }
}

async function readBackendActivity(backend: BackendSupervisor): Promise<BackendActivity | null> {
  // A remote backend is not ours to keep alive; only the clock decides.
  if (backend.connection.remote) return { inflight: 0, busy: [], idle_seconds: Infinity };
  try {
    const response = await fetch(`${backend.baseUrl}/system/activity`, {
      headers: backend.requestHeaders(),
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) return null;
    const body = (await response.json()) as Partial<BackendActivity>;
    return typeof body.inflight === 'number' &&
      Array.isArray(body.busy) &&
      typeof body.idle_seconds === 'number'
      ? (body as BackendActivity)
      : null;
  } catch {
    return null;
  }
}

function terminalCommandOptions(): TerminalCommandOptions {
  return {
    platform: process.platform,
    home: homedir(),
    resources: process.resourcesPath,
    appImage: process.env.APPIMAGE,
    snap: process.env.SNAP,
    path: process.env.PATH,
  };
}

async function tearDownNativeResources(): Promise<void> {
  idleShutdown?.stop();
  idleShutdown = null;
  publishedDiscovery = '';
  try {
    clearDiscovery(DISCOVERY_DIR, process.pid);
  } catch {
    /* a stale record is harmless: terminal commands verify the backend */
  }
  closeWatch?.();
  closeWatch = null;
  closeCapture?.();
  closeCapture = null;
  closeUpdates?.();
  closeUpdates = null;
  closeRepairAgents?.();
  closeRepairAgents = null;
  closeAccount?.();
  closeAccount = null;
  tray?.destroy();
  tray = null;
  const proxyTeardown = closeDevProxy?.() ?? Promise.resolve();
  closeDevProxy = null;
  await Promise.allSettled([supervisor?.shutdown() ?? Promise.resolve(), proxyTeardown]);
  closeDirectoryScopes?.();
  closeDirectoryScopes = null;
}

function beginOrderlyQuit(flushPersistence = true): Promise<void> {
  if (quitPreparation) return quitPreparation;
  quitPreparation = (async () => {
    if (flushPersistence) await flushRendererPersistence();
    const teardown = tearDownNativeResources();
    const timeout = new Promise<void>((resolve) => setTimeout(resolve, 5000));
    await Promise.race([teardown, timeout]);
    quitting = true;
    if (relaunchIntent) {
      // A launch arrived while this instance was stopping: start it afresh.
      // An AppImage's mount disappears with this process, so relaunch the image.
      const args = relaunchArguments(relaunchIntent);
      app.relaunch(process.env.APPIMAGE ? { execPath: process.env.APPIMAGE, args } : { args });
    }
    app.quit();
  })();
  return quitPreparation;
}

function brandIconPath(file: string): string {
  return app.isPackaged
    ? join(process.resourcesPath, 'brand', file)
    : join(backendRoot(), 'electron', 'build', 'icons', file);
}

ipcMain.handle('terminal:status', (event) => {
  assertTrustedMainFrame(event, mainWindow);
  return terminalCommandStatus(terminalCommandOptions());
});
ipcMain.handle('terminal:add-to-path', (event) => {
  assertTrustedMainFrame(event, mainWindow);
  return addTerminalCommandToPath(terminalCommandOptions());
});

ipcMain.handle(
  'app:is-fullscreen',
  (event) => BrowserWindow.fromWebContents(event.sender)?.isFullScreen() ?? false,
);

function createWindow(): BrowserWindow {
  const appUrl =
    is.dev && process.env.ELECTRON_RENDERER_URL
      ? process.env.ELECTRON_RENDERER_URL
      : `${APP_ORIGIN}/index.html`;
  const win = new BrowserWindow({
    icon: brandIconPath(process.platform === 'win32' ? 'icon.ico' : 'icon.png'),
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    show: false,
    backgroundColor: BACKGROUND,
    titleBarStyle: 'hidden',
    ...(process.platform === 'darwin'
      ? { trafficLightPosition: MAC_TRAFFIC_LIGHTS }
      : { titleBarOverlay: OVERLAY }),
    webPreferences: {
      preload: PRELOAD_PATH,
      contextIsolation: true,
      // ESM preload scripts (electron-vite emits .mjs) require an unsandboxed renderer.
      sandbox: false,
      nodeIntegration: false,
    },
  });

  win.on('enter-full-screen', () => win.webContents.send('app:fullscreen', true));
  win.on('leave-full-screen', () => win.webContents.send('app:fullscreen', false));
  win.on('ready-to-show', () => activateLiveWindow(win));
  win.on('close', (event) => {
    if (quitting) return;
    event.preventDefault();
    // A terminal-started app returns to headless; its idle timer then decides.
    if (process.platform === 'darwin' || headlessOwner) {
      observeMainProcessTask(
        flushRendererPersistence().finally(() => {
          if (!win.isDestroyed()) win.destroy();
        }),
        mainErrors,
        'Window persistence flush',
      );
      return;
    }
    observeMainProcessTask(beginOrderlyQuit(), mainErrors, 'Orderly quit');
  });
  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null;
    if (headlessOwner) {
      if (process.platform === 'darwin') app.dock?.hide();
      idleShutdown?.touch();
      publishDiscovery();
      return;
    }
    if (process.platform !== 'darwin' && !quitting) app.quit();
  });
  wireWindowMaximizeEvents(win);
  installBlankWindowGuard(
    win,
    appUrl,
    app.getLocale(),
    process.env.ELECTRON_RENDERER_URL,
    async () => {
      await win.webContents.session.clearCache();
      app.relaunch();
      // The independent fallback has no mounted persistence handler to flush.
      await beginOrderlyQuit(false);
    },
  );

  win.webContents.setWindowOpenHandler(({ url }) => {
    void openOutside(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    if (isOwnUrl(url)) return;
    event.preventDefault();
    void openOutside(url);
  });

  if (is.dev && process.env.ELECTRON_RENDERER_URL) {
    // The blank-window guard owns bounded renderer recovery; consume the load
    // promise so a transient dev-server/package read failure is not uncaught.
    void win.loadURL(appUrl).catch(() => {});
    if (shouldOpenDevTools()) win.webContents.openDevTools({ mode: 'detach' });
  } else {
    void win.loadURL(appUrl).catch(() => {});
  }
  return win;
}

/** Open (or focus) the window in this process, e.g. for a launch from the menu. */
function showWindow(navigateTo?: string): void {
  if (quitPreparation) return;
  if (!isLiveWindow(mainWindow)) mainWindow = createWindow();
  if (process.platform === 'darwin') void app.dock?.show();
  activateLiveWindow(mainWindow);
  idleShutdown?.touch();
  publishDiscovery();
  const win = mainWindow;
  if (navigateTo && win) {
    const navigate = () => sendToLiveWindow(win, 'app:navigate', navigateTo);
    if (win.webContents.isLoading()) win.webContents.once('did-finish-load', navigate);
    else navigate();
  }
}

/** Act on a later launch of the app, delivered by the single-instance lock. */
function handleLaunch(intent: LaunchIntent): void {
  if (quitPreparation) {
    if (!intent.stop) relaunchIntent = intent;
    return;
  }
  if (intent.stop) {
    // `voicestudio stop` never closes a window the user is working in.
    if (!isLiveWindow(mainWindow))
      observeMainProcessTask(beginOrderlyQuit(false), mainErrors, 'Terminal stop');
    return;
  }
  if (intent.checkUpdates) {
    showWindow('/settings/updates');
    checkForUpdates?.();
    return;
  }
  if (intent.headless) {
    // Another terminal command: already running, so this only counts as use.
    idleShutdown?.touch();
    return;
  }
  showWindow();
}

function isOwnUrl(url: string): boolean {
  const dev = process.env.ELECTRON_RENDERER_URL;
  return isTrustedRenderer(url, dev);
}

async function openOutside(url: string): Promise<void> {
  try {
    const parsed = new URL(url);
    if (
      parsed.protocol === 'http:' ||
      parsed.protocol === 'https:' ||
      parsed.protocol === 'mailto:'
    ) {
      await shell.openExternal(parsed.toString());
    }
  } catch {
    /* unparsable URLs are simply dropped */
  }
}

if (process.env.VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES !== '1' && !app.requestSingleInstanceLock()) {
  app.quit();
} else if (launchIntent.stop) {
  // `--stop` with nothing running: there is nothing to stop or start.
  app.exit(0);
} else {
  let initialized: Promise<void> = Promise.resolve();
  app.on('second-instance', (_event, argv) => {
    const intent = parseLaunchIntent(argv);
    void initialized.then(() => handleLaunch(intent));
  });

  initialized = app
    .whenReady()
    .then(async () => {
      electronApp.setAppUserModelId(APP_ID);
      if (app.isPackaged) {
        try {
          setUpTerminalCommand(
            terminalCommandOptions(),
            join(app.getPath('userData'), TERMINAL_COMMAND_STATE),
          );
        } catch (error) {
          console.warn('[terminal-command] Could not set up the terminal command:', error);
        }
      }
      if (headlessOwner && process.platform === 'darwin') app.dock?.hide();
      if (process.platform === 'darwin') installMacApplicationMenu(app, Menu, __APP_VERSION__);
      installRendererPermissions(session.defaultSession, process.env.ELECTRON_RENDERER_URL);
      app.on('browser-window-created', (_event, window) => optimizer.watchWindowShortcuts(window));
      closeDirectoryScopes = restoreSecurityScopedDirectories();

      const backend = new BackendSupervisor();
      supervisor = backend;
      console.log(`[supervisor] backend root: ${backendRoot()} (packaged=${app.isPackaged})`);
      backend.subscribe((s) => {
        const detail = s.message ? ` — ${s.message}` : '';
        console.log(`[supervisor] stage=${s.stage} managed=${s.managed} port=${s.port}${detail}`);
        publishDiscovery();
      });
      installAppProtocol(
        () => backend.baseUrl,
        RENDERER_DIR,
        () => backend.requestHeaders(),
      );
      registerIpc(
        backend,
        () => mainWindow,
        async () => {
          quitting = true;
          await tearDownNativeResources();
          app.exit(0);
        },
      );
      // Before backend.start(): the backend inherits the token-bridge env.
      closeAccount = await startAccount({
        getMainWindow: () => mainWindow,
        isRemoteBackend: () => backend.connection.remote,
        notifyBackend: async (path) => {
          if (backend.connection.remote) return;
          await fetch(`${backend.baseUrl}${path}`, {
            method: 'POST',
            headers: backend.requestHeaders(),
            signal: AbortSignal.timeout(5000),
          });
        },
      });
      closeRepairAgents = await registerRepairAgents(
        backend,
        backendRoot(),
        () => mainWindow,
        () => mainErrors.recent(),
      );
      if (is.dev) {
        const proxy = await startDevBackendProxy(
          () => backend.baseUrl,
          () => backend.requestHeaders(),
          electronProxyPort(process.env.VOICESTUDIO_ELECTRON_PROXY_PORT),
          process.env.ELECTRON_RENDERER_URL
            ? [new URL(process.env.ELECTRON_RENDERER_URL).origin]
            : [],
        );
        closeDevProxy = proxy.close;
      }
      const updater = new DesktopUpdater();
      const closeUpdateIpc = registerUpdateIpc(
        updater,
        () => mainWindow,
        async () => {
          await flushRendererPersistence();
          await tearDownNativeResources();
          quitting = true;
        },
      );
      const startupUpdateCheck = setTimeout(
        () => observeMainProcessTask(updater.check(true), mainErrors, 'Startup update check'),
        10_000,
      );
      const periodicUpdateCheck = setInterval(
        () => observeMainProcessTask(updater.check(true), mainErrors, 'Periodic update check'),
        6 * 60 * 60 * 1_000,
      );
      checkForUpdates = () =>
        observeMainProcessTask(updater.check(false), mainErrors, 'Terminal update check');
      closeUpdates = () => {
        clearTimeout(startupUpdateCheck);
        clearInterval(periodicUpdateCheck);
        closeUpdateIpc();
      };
      if (!headlessOwner) mainWindow = createWindow();
      if (launchIntent.checkUpdates) handleLaunch(launchIntent);
      closeWatch = installWatchFolders(
        () => mainWindow,
        () => backend.baseUrl,
        () => backend.requestHeaders(),
      );
      const icon = nativeImage.createFromPath(brandIconPath('icon-macos.png'));
      if (process.platform === 'darwin') app.dock?.setIcon(icon);
      tray = new Tray(createTrayIcon(brandIconPath('32x32.png')));
      tray.setToolTip(PRODUCT_NAME);
      closeCapture = installNativeCapture(PRELOAD_PATH, tray, () => mainWindow);
      tray.on('click', () => showWindow());
      observeMainProcessTask(backend.start(), mainErrors, 'Backend startup');
      if (headlessOwner) {
        idleShutdown = new IdleShutdown({
          idleMs: idleShutdownMs(),
          hasWindow: () => isLiveWindow(mainWindow),
          stage: () => backend.status.stage,
          readActivity: () => readBackendActivity(backend),
          onIdle: () => {
            console.log('[headless] No terminal use for the idle period; stopping.');
            observeMainProcessTask(beginOrderlyQuit(false), mainErrors, 'Idle shutdown');
          },
        });
        idleShutdown.start();
      }

      app.on('activate', () => {
        if (!isLiveWindow(mainWindow)) showWindow();
      });
    })
    .catch((error) => {
      // Keep initialization failures out of Node's unhandled-rejection path,
      // while retaining durable evidence for the next launch and repair agent.
      mainErrors.record(error, 'unhandledRejection');
      console.error('[startup] Electron initialization failed', error);
      app.exit(1);
    });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin' && !headlessOwner) app.quit();
  });

  // The first pass gives renderer persistence a bounded chance to settle, then
  // tears native resources down. The second pass exits without re-entering.
  app.on('before-quit', (event) => {
    if (quitting) return;
    event.preventDefault();
    observeMainProcessTask(beginOrderlyQuit(), mainErrors, 'Orderly quit');
  });
}
