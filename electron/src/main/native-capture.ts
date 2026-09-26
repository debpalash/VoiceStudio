import { readFileSync } from 'node:fs';
import { writeFile, rename } from 'node:fs/promises';
import { ShortcutSettings } from './shortcut-settings';
import {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  screen,
  type Tray,
  type IpcMainInvokeEvent,
} from 'electron';
import { join } from 'node:path';
import { backendRoot } from './backend';
import { APP_ORIGIN } from './protocol';
import { isTrustedRenderer } from './trusted-renderer';
import { DictationOutputClient } from './dictation-output';
import { CaptureSession, type CapturePhase } from './capture-session';
import { activateLiveWindow, sendToLiveWindow } from './window-safety';

/** Dedicated recorder owns the output IPC; ordinary app frames cannot type text. */
export function installNativeCapture(
  preload: string,
  tray: Tray,
  main: () => BrowserWindow | null,
): () => void {
  let recorder: BrowserWindow | null = null;
  let unregister: (() => void) | null = null;
  let capturing = false;
  let trayLabels: {
    show: string;
    start: string;
    stop: string;
    settings: string;
    exit: string;
  } | null = null;
  const name = 'voicestudio-desktop-bridge' + (process.platform === 'win32' ? '.exe' : '');
  const executable = app.isPackaged
    ? join(process.resourcesPath, 'native', name)
    : join(backendRoot(), 'native', 'desktop-bridge', 'target', 'debug', name);
  const trayIcon = (file: string) =>
    app.isPackaged
      ? join(process.resourcesPath, 'brand', file)
      : join(backendRoot(), 'electron', 'build', 'icons', file);
  const settingsPath = join(app.getPath('userData'), 'dictation-shortcut.json');
  let saved: unknown;
  try {
    saved = JSON.parse(readFileSync(settingsPath, 'utf8')).accelerator;
  } catch {
    /* first run */
  }
  const output = new DictationOutputClient(executable, process.pid, (event) => {
    if (!shortcuts.enabled) {
      if (event.pressed)
        void output.request({ method: 'reject', session: event.session }).catch(() => {});
      return;
    }
    if (event.pressed) {
      sendToLiveWindow(main(), 'capture:shortcutPressed', shortcuts.getState().accelerator);
    }
    void capture.shortcut(event, shortcuts.mode).catch(() => {
      void capture.cancel().catch(() => {});
    });
  });
  const shortcuts = new ShortcutSettings(output, saved, async (accelerator) => {
    const temporary = settingsPath + '.tmp';
    await writeFile(temporary, JSON.stringify({ accelerator }), 'utf8');
    await rename(temporary, settingsPath);
  });
  const capture = new CaptureSession(
    output,
    () => {
      const win = recorder ?? createRecorder();
      const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
      win.setPosition(Math.round(area.x + (area.width - 480) / 2), area.y + area.height - 240);
      win.showInactive();
    },
    () => {
      if (recorder && !recorder.isDestroyed()) recorder.hide();
    },
  );

  const showMain = (path?: '/settings') => {
    const owner = main();
    if (!activateLiveWindow(owner)) return;
    if (path) sendToLiveWindow(owner, 'app:navigate', path);
  };
  const refreshTrayMenu = () => {
    if (!trayLabels) return;
    const labels = trayLabels;
    const shortcut = shortcuts.getState().accelerator;
    tray.setImage(trayIcon(capturing ? 'tray-recording.png' : '32x32.png'));
    tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: labels.show, click: () => showMain() },
        { type: 'separator' },
        {
          label: `${capturing ? labels.stop : labels.start}  ${shortcut}`,
          enabled: capturing || capture.canStart(),
          click: () => {
            if (capturing) {
              capture.stop();
              return;
            }
            if (!capture.canStart()) return;
            capturing = true;
            refreshTrayMenu();
            void capture.start('tray').catch(() => {
              capturing = false;
              refreshTrayMenu();
              void capture.cancel().catch(() => {});
            });
          },
        },
        { label: labels.settings, click: () => showMain('/settings') },
        { type: 'separator' },
        { label: labels.exit, click: () => app.quit() },
      ]),
    );
  };

  function createRecorder(): BrowserWindow {
    const win = new BrowserWindow({
      width: 480,
      height: 210,
      minWidth: 480,
      minHeight: 210,
      show: false,
      frame: false,
      resizable: false,
      alwaysOnTop: true,
      skipTaskbar: true,
      backgroundColor: '#0a0a0a',
      webPreferences: {
        preload,
        contextIsolation: true,
        sandbox: false,
        nodeIntegration: false,
        backgroundThrottling: false,
      },
    });
    recorder = win;
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.webContents.on('will-navigate', (event) => event.preventDefault());
    const disconnected = () => {
      unregister?.();
      unregister = null;
      void capture.cancel().catch(() => {});
    };
    win.webContents.on('render-process-gone', disconnected);
    win.webContents.on('did-start-loading', () => {
      if (unregister) disconnected();
    });
    win.on('closed', () => {
      disconnected();
      if (recorder === win) recorder = null;
    });
    const origin = process.env.ELECTRON_RENDERER_URL || `${APP_ORIGIN}/index.html`;
    void win.loadURL(`${origin.split('#')[0]}#/capture`).catch(() => {
      if (!win.isDestroyed()) win.destroy();
    });
    return win;
  }
  function assertRecorder(event: IpcMainInvokeEvent): void {
    if (
      !recorder ||
      event.sender !== recorder.webContents ||
      event.senderFrame !== event.sender.mainFrame ||
      !isTrustedRenderer(event.senderFrame.url, process.env.ELECTRON_RENDERER_URL)
    )
      throw new Error('Untrusted recorder');
  }
  const handle = (
    name: string,
    action: (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown,
  ) => {
    ipcMain.handle(`capture:${name}`, (event, ...args: unknown[]) => {
      assertRecorder(event);
      return action(event, ...args);
    });
  };
  handle('ready', () => {
    if (!unregister)
      unregister = capture.listen((event) => {
        capturing = event.action === 'start';
        refreshTrayMenu();
        sendToLiveWindow(recorder, 'capture:event', event);
      });
  });
  handle('accept', (_event, id) => capture.accept(Number(id)));
  handle('deliver', (_event, id, sequence, text) => {
    if (typeof text !== 'string') throw new Error('Invalid transcript');
    return capture.deliver(Number(id), Number(sequence), text);
  });
  handle('phase', (_event, id, phase) => {
    if (
      typeof id !== 'number' ||
      !['starting', 'recording', 'transcribing', 'done', 'error'].includes(String(phase))
    )
      throw new Error('Invalid capture phase');
    capture.phase(id, phase as CapturePhase);
    refreshTrayMenu();
  });
  handle('finish', (_event, id) => capture.finish(Number(id)));
  handle('cancel', (_event, id) => {
    if (id !== null && typeof id !== 'number') throw new Error('Invalid capture session');
    return capture.cancelFor(id);
  });
  handle('stop', () => capture.stop());
  function assertMain(event: IpcMainInvokeEvent): void {
    const owner = main();
    if (
      !owner ||
      event.sender !== owner.webContents ||
      event.senderFrame !== event.sender.mainFrame ||
      !isTrustedRenderer(event.senderFrame.url, process.env.ELECTRON_RENDERER_URL)
    )
      throw new Error('Untrusted shortcut settings');
  }
  ipcMain.handle('capture:getShortcut', (event) => {
    assertMain(event);
    return shortcuts.getState();
  });
  ipcMain.handle('capture:setShortcut', async (event, accelerator: unknown) => {
    assertMain(event);
    if (typeof accelerator !== 'string') throw new Error('Invalid shortcut');
    const result = await shortcuts.set(accelerator);
    refreshTrayMenu();
    return result;
  });
  ipcMain.handle('capture:syncPreferences', async (event, prefs: unknown) => {
    assertMain(event);
    if (!prefs || typeof prefs !== 'object') throw new Error('Invalid dictation preferences');
    const { enabled, mode } = prefs as { enabled?: unknown; mode?: unknown };
    if (typeof enabled !== 'boolean' || (mode !== 'hold' && mode !== 'toggle'))
      throw new Error('Invalid dictation preferences');
    const result = await shortcuts.synchronize(enabled, mode);
    refreshTrayMenu();
    return result;
  });
  ipcMain.handle('capture:labels', (event, labels: unknown) => {
    const owner = main();
    if (
      !owner ||
      event.sender !== owner.webContents ||
      event.senderFrame !== event.sender.mainFrame ||
      !isTrustedRenderer(event.senderFrame.url, process.env.ELECTRON_RENDERER_URL)
    )
      throw new Error('Untrusted tray labels');
    if (!labels || typeof labels !== 'object') throw new Error('Invalid labels');
    const { show, start, stop, settings, exit } = labels as Record<string, unknown>;
    if (
      typeof show !== 'string' ||
      typeof start !== 'string' ||
      typeof stop !== 'string' ||
      typeof settings !== 'string' ||
      typeof exit !== 'string' ||
      !show ||
      !start ||
      !stop ||
      !settings ||
      !exit ||
      show.length > 120 ||
      start.length > 120 ||
      stop.length > 120 ||
      settings.length > 120 ||
      exit.length > 120
    )
      throw new Error('Invalid labels');
    trayLabels = { show, start, stop, settings, exit };
    refreshTrayMenu();
  });
  const prime = () => {
    void output.request({ method: 'prime_tray' }).catch(() => {});
  };
  tray.on('mouse-enter', prime);
  return () => {
    tray.removeListener('mouse-enter', prime);
    unregister?.();
    unregister = null;
    void capture
      .cancel()
      .catch(() => {})
      .finally(() => output.close());
    if (recorder && !recorder.isDestroyed()) recorder.destroy();
    recorder = null;
    for (const name of [
      'ready',
      'accept',
      'deliver',
      'finish',
      'cancel',
      'stop',
      'labels',
      'phase',
      'getShortcut',
      'setShortcut',
      'syncPreferences',
    ])
      ipcMain.removeHandler(`capture:${name}`);
  };
}
