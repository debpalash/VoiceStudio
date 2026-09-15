import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';
import type {
  BackendStatus,
  DataRelocationStage,
  SaveAudioRequest,
  SaveAudioResult,
  UpdateState,
  UpdateReleaseInfo,
  VoiceStudioBridge,
  RepairAgentEvent,
} from './index.d';

function subscribe<T>(channel: string, cb: (payload: T) => void): () => void {
  const listener = (_event: IpcRendererEvent, payload: T) => cb(payload);
  ipcRenderer.on(channel, listener);
  return () => {
    ipcRenderer.removeListener(channel, listener);
  };
}

const bridge: VoiceStudioBridge = {
  repair: {
    list: () => ipcRenderer.invoke('repair:list'),
    getState: () => ipcRenderer.invoke('repair:getState'),
    chooseWorkspace: () => ipcRenderer.invoke('repair:chooseWorkspace'),
    start: (request) => ipcRenderer.invoke('repair:start', request),
    stop: () => ipcRenderer.invoke('repair:stop'),
    translate: (request) => ipcRenderer.invoke('repair:translate', request),
    stopTranslation: () => ipcRenderer.invoke('repair:stopTranslation'),
    onTranslationEvent: (callback) => subscribe('repair:translationEvent', callback),
    onEvent: (callback) => subscribe<RepairAgentEvent>('repair:event', callback),
  },
  permissions: {
    getState: () => ipcRenderer.invoke('permissions:getState'),
    openSettings: (kind) => ipcRenderer.invoke('permissions:openSettings', kind),
  },
  updates: {
    getState: () => ipcRenderer.invoke('updates:getState') as Promise<UpdateState>,
    check: () => ipcRenderer.invoke('updates:check') as Promise<UpdateState>,
    download: () => ipcRenderer.invoke('updates:download') as Promise<UpdateState>,
    dismiss: () => ipcRenderer.invoke('updates:dismiss') as Promise<UpdateState>,
    install: () => ipcRenderer.invoke('updates:install') as Promise<void>,
    setChannel: (channel) =>
      ipcRenderer.invoke('updates:setChannel', channel) as Promise<UpdateState>,
    listReleases: (channel) =>
      ipcRenderer.invoke('updates:listReleases', channel) as Promise<UpdateReleaseInfo[]>,
    onState: (cb) => subscribe<UpdateState>('updates:state', cb),
  },
  watch: {
    pick: () => ipcRenderer.invoke('watch:pick'),
    scan: (token) => ipcRenderer.invoke('watch:scan', token),
    enqueue: (request) => ipcRenderer.invoke('watch:enqueue', request),
    stop: (token) => ipcRenderer.invoke('watch:stop', token),
  },
  app: {
    version: __APP_VERSION__,
    platform: process.platform,
    isDev: process.env.NODE_ENV === 'development' || !!process.env.ELECTRON_RENDERER_URL,
    onNavigate: (callback) => subscribe<string>('app:navigate', callback),
    onPersistenceFlush: (callback) => {
      const listener = (_event: IpcRendererEvent, token: string) => {
        void (async () => {
          try {
            await callback();
          } finally {
            ipcRenderer.send('app:persistence-flushed', token);
          }
        })().catch(() => {});
      };
      ipcRenderer.on('app:flush-persistence', listener);
      return () => ipcRenderer.removeListener('app:flush-persistence', listener);
    },
  },
  backend: {
    getStatus: () => ipcRenderer.invoke('backend:getStatus') as Promise<BackendStatus>,
    acknowledgeCrash: () => ipcRenderer.invoke('backend:acknowledgeCrash'),
    onStatus: (cb) => subscribe<BackendStatus>('backend:status', cb),
    setupRuntime: () => ipcRenderer.invoke('backend:setupRuntime') as Promise<void>,
    cleanSetupRuntime: () => ipcRenderer.invoke('backend:cleanSetupRuntime') as Promise<void>,
    setRuntimeRegion: (region) => ipcRenderer.invoke('backend:setRuntimeRegion', region),
    chooseRuntimeLocation: (title) => ipcRenderer.invoke('backend:chooseRuntimeLocation', title),
    useDefaultRuntimeLocation: () => ipcRenderer.invoke('backend:useDefaultRuntimeLocation'),
    restart: () => ipcRenderer.invoke('backend:restart') as Promise<void>,
    getConnection: () => ipcRenderer.invoke('backend:getConnection'),
    testRemote: (input) => ipcRenderer.invoke('backend:testRemote', input),
    useRemote: (input) => ipcRenderer.invoke('backend:useRemote', input),
    useLocal: () => ipcRenderer.invoke('backend:useLocal'),
    websocketUrl: (path) => ipcRenderer.invoke('backend:websocketUrl', path),
  },
  maintenance: {
    scanReset: () => ipcRenderer.invoke('maintenance:scanReset'),
    purgeReset: (scopes) => ipcRenderer.invoke('maintenance:purgeReset', scopes),
    chooseDataDirectory: () => ipcRenderer.invoke('maintenance:chooseDataDirectory'),
    relocateDataDirectory: (authorization) =>
      ipcRenderer.invoke('maintenance:relocateDataDirectory', authorization),
    onRelocationProgress: (cb) =>
      subscribe<{ stage: DataRelocationStage }>('maintenance:relocationProgress', ({ stage }) =>
        cb(stage),
      ),
    scanUninstall: () => ipcRenderer.invoke('maintenance:scanUninstall'),
    purgeUninstall: (includeModels) =>
      ipcRenderer.invoke('maintenance:purgeUninstall', includeModels),
  },
  capture: {
    phase: (session, phase) => ipcRenderer.invoke('capture:phase', session, phase),
    getShortcut: () => ipcRenderer.invoke('capture:getShortcut'),
    onShortcutPressed: (callback) => subscribe<string>('capture:shortcutPressed', callback),
    setShortcut: (accelerator) => ipcRenderer.invoke('capture:setShortcut', accelerator),
    syncPreferences: (prefs) => ipcRenderer.invoke('capture:syncPreferences', prefs),
    ready: () => ipcRenderer.invoke('capture:ready'),
    accept: (session) => ipcRenderer.invoke('capture:accept', session),
    deliver: (session, sequence, text) =>
      ipcRenderer.invoke('capture:deliver', session, sequence, text),
    finish: (session) => ipcRenderer.invoke('capture:finish', session),
    cancel: (session) => ipcRenderer.invoke('capture:cancel', session),
    stop: () => ipcRenderer.invoke('capture:stop'),
    labels: (labels) => ipcRenderer.invoke('capture:labels', labels),
    onEvent: (callback) => subscribe('capture:event', callback),
  },
  files: {
    authorizeMediaTool: (kind) => ipcRenderer.invoke('files:authorizeMediaTool', kind),
    authorizeModelsDirectory: (reset = false) =>
      ipcRenderer.invoke('files:authorizeModelsDirectory', reset),
    extractScript: (input) => ipcRenderer.invoke('files:extractScript', input) as Promise<string>,
    saveAudio: (req: SaveAudioRequest) =>
      ipcRenderer.invoke('files:saveAudio', req) as Promise<SaveAudioResult>,
    saveData: (req) => ipcRenderer.invoke('files:saveData', req) as Promise<SaveAudioResult>,
    revealPath: (path) => ipcRenderer.invoke('files:revealPath', path) as Promise<void>,
    openExternal: (url) => ipcRenderer.invoke('files:openExternal', url) as Promise<void>,
  },
  window: {
    minimize: () => ipcRenderer.send('window:minimize'),
    toggleMaximize: () => ipcRenderer.send('window:toggleMaximize'),
    close: () => ipcRenderer.send('window:close'),
    onMaximizedChange: (cb) => subscribe<boolean>('window:maximized', cb),
    isMaximized: () => ipcRenderer.invoke('window:isMaximized') as Promise<boolean>,
  },
};

contextBridge.exposeInMainWorld('voicestudio', bridge);
