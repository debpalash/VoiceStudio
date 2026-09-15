/**
 * The contextBridge surface exposed to the renderer as `window.voicestudio`.
 *
 * Keep this file the single contract between main (src/main), preload
 * (src/preload/index.ts) and the renderer. Both tsconfigs include it.
 */

export type BackendStage =
  | 'setup_required'
  | 'installing'
  | 'idle'
  | 'attaching' // an already-running backend on the port answered → we reuse it
  | 'starting' // we spawned it and are polling /system/info
  | 'ready'
  | 'crashed' // the spawned process exited unexpectedly
  | 'port_in_use' // backend exited 78 (EX_CONFIG): another process holds the port
  | 'failed'; // could not start within the budget / spawn error

export interface NativeCrashRecord {
  timestamp: number;
  version: string;
  exitCode: number | null;
  signal: string | null;
  uptimeMs: number;
  logTail: string[];
  /** Viewing or dismissing a crash marks it seen while retaining evidence for reports. */
  acknowledged: boolean;
}
export interface BackendStatus {
  lastCrash?: NativeCrashRecord;
  stage: BackendStage;
  setupIssue?: 'space' | 'access';
  /** A prior explicit install stopped after creating its resumable project/cache. */
  runtimeInterrupted?: boolean;
  setupPhase?: 'checking' | 'downloading_uv' | 'installing_deps' | 'verifying';
  setupProgress?: {
    resolvedPackages?: number;
    preparedPackages?: number;
    installedPackages?: number;
    completedDownloads: number;
    downloadedBytes?: number;
    totalBytes?: number;
    bytesPerSecond?: number;
    etaSeconds?: number;
    transferUpdatedAt?: number;
    activityUpdatedAt?: number;
    activePackage?: string;
    downloadsComplete?: boolean;
    estimatedBytes?: boolean;
  };
  /** http://127.0.0.1:<port> — no trailing slash. */
  baseUrl: string;
  port: number;
  /** True when this shell spawned the process (vs. attached to an external one). */
  managed: boolean;
  /** True when API traffic is routed to a user-configured remote backend. */
  remote: boolean;
  /** Human-readable detail for failed/crashed/port_in_use. */
  message?: string;
  exitCode?: number | null;
  /** Milliseconds since the spawn/attach attempt started. */
  elapsedMs: number;
  /** Last ~40 lines of backend stdout/stderr (newest last) for the splash log. */
  logTail: string[];
  /** Managed Python environment root shown during first-run setup. */
  runtimePath?: string;
  runtimeCustom?: boolean;
  runtimeRegion?: RuntimeRegion;
}

export type RuntimeRegion = 'auto' | 'global' | 'china' | 'russia' | 'restricted';

export interface RuntimeLocation {
  path: string;
  custom: boolean;
}

export interface BackendConnection {
  remote: boolean;
  url: string;
  /** A scoped remote administrator session is currently held in main memory. */
  authenticated: boolean;
}

export type UpdateChannel = 'stable' | 'preview';
export type UpdateStatus =
  | 'unsupported'
  | 'idle'
  | 'checking'
  | 'available'
  | 'downloading'
  | 'downloaded'
  | 'error';
export interface UpdateState {
  status: UpdateStatus;
  currentVersion: string;
  availableVersion?: string;
  channel: UpdateChannel;
  notes?: string | null;
  progress: number;
  transferredBytes?: number;
  totalBytes?: number;
  bytesPerSecond?: number;
  etaSeconds?: number;
  lastCheckedAt?: number;
  error?: string;
}
export interface UpdateReleaseInfo {
  version: string;
  name: string;
  date: string;
  prerelease: boolean;
  notes: string;
}

export type RemoteBackendProbe =
  | { ok: true; detail: string; target: string; authenticated: boolean }
  | {
      ok: false;
      kind: 'invalid' | 'tls' | 'network' | 'timeout' | 'http' | 'wrong_port' | 'auth';
      status?: number;
      target: string;
    };

export interface SaveAudioRequest {
  /** App-relative API path (`/api/audio/<id>.wav`) or an absolute backend URL.
   *  Main resolves app-relative paths against the live backend base. */
  url: string;
  suggestedName: string;
  /** Backend request method. POST supports generated download endpoints such as persona export. */
  method?: 'GET' | 'POST';
}

export interface SaveAudioResult {
  canceled: boolean;
  path?: string;
}

export interface SaveDataRequest {
  data: Uint8Array;
  suggestedName: string;
}

export type RepairAgentId = 'codex' | 'claude' | 'opencode' | 'pi';
export type RepairAgentStatus = 'idle' | 'running' | 'complete' | 'failed' | 'stopped';
export interface RepairAgentInfo {
  id: RepairAgentId;
  label: string;
  available: boolean;
  version: string;
}
export interface RepairAgentRunRequest {
  agent: RepairAgentId;
  mode: 'diagnose' | 'fix';
  report: string;
  context: string;
}
export interface DubAgentTranslationSegment {
  id: string;
  sourceText: string;
  currentText?: string;
  start: number;
  end: number;
  measuredSeconds?: number;
}
export interface DubAgentTranslationRequest {
  requestId?: string;
  agent: RepairAgentId;
  purpose: 'translate' | 'fit';
  sourceLanguage?: string;
  targetLanguage: string;
  dialect?: string;
  translationInstructions?: string;
  glossary?: Array<{ source: string; target: string; note?: string }>;
  segments: DubAgentTranslationSegment[];
}
export interface DubAgentTranslationResult {
  agent: RepairAgentId;
  translations: Array<{ id: string; text: string }>;
}
export interface RepairAgentState {
  sessionId?: string;
  agent?: RepairAgentId;
  mode?: 'diagnose' | 'fix';
  status: RepairAgentStatus;
  output: string;
  exitCode?: number | null;
  workspaceAvailable: boolean;
  workspacePath?: string;
}
export type RepairAgentEvent =
  | { sessionId: string; type: 'output'; text: string }
  | { sessionId: string; type: 'state'; status: RepairAgentStatus; exitCode?: number | null };

export type NativePermissionStatus = 'granted' | 'denied' | 'prompt' | 'unknown';

export interface NativePermissions {
  platform: NodeJS.Platform;
  microphone: NativePermissionStatus;
  accessibility?: 'granted' | 'denied';
}

export interface AuthorizedPathSelection {
  authorization: string;
  path: string;
}

export interface WatchEntry {
  name: string;
  size: number;
  mtime: number;
}
export interface WatchSelection {
  token: string;
  path: string;
}
export interface WatchUpload {
  token: string;
  entry: WatchEntry;
  langs: string[];
  voiceId: string;
  preserveBg: boolean;
}

export interface ResetScope {
  key: string;
  paths: string[];
  size_bytes: number;
  exists: boolean;
  shared: boolean;
  needs_restart: boolean;
}

export interface ResetReport {
  removed: string[];
  failed: string[];
  refused: string[];
  freed_bytes: number;
  restarted: boolean;
}

export interface DataDirectorySelection {
  authorization: string;
  source: string;
  target: string;
  size_bytes: number;
  file_count: number;
}

export interface DataRelocationResult {
  path: string;
  size_bytes: number;
  file_count: number;
  removed_source: boolean;
}

export type DataRelocationStage =
  | 'stopping'
  | 'copying'
  | 'switching'
  | 'restarting'
  | 'cleaning'
  | 'rolling_back'
  | 'done';

export interface UninstallTarget {
  key: 'data' | 'env' | 'logs' | 'userenv' | 'models';
  path: string;
  size_bytes: number;
  exists: boolean;
  shared: boolean;
}

export interface VoiceStudioBridge {
  repair: {
    list(): Promise<RepairAgentInfo[]>;
    getState(): Promise<RepairAgentState>;
    chooseWorkspace(): Promise<RepairAgentState>;
    start(request: RepairAgentRunRequest): Promise<{ sessionId: string }>;
    stop(): Promise<RepairAgentState>;
    translate(request: DubAgentTranslationRequest): Promise<DubAgentTranslationResult>;
    stopTranslation(): Promise<void>;
    onTranslationEvent(callback: (event: { requestId: string; text: string }) => void): () => void;
    onEvent(callback: (event: RepairAgentEvent) => void): () => void;
  };
  permissions: {
    getState(): Promise<NativePermissions>;
    openSettings(kind: 'microphone' | 'accessibility'): Promise<boolean>;
  };
  updates: {
    getState(): Promise<UpdateState>;
    check(): Promise<UpdateState>;
    download(): Promise<UpdateState>;
    dismiss(): Promise<UpdateState>;
    install(): Promise<void>;
    setChannel(channel: UpdateChannel): Promise<UpdateState>;
    listReleases(channel: UpdateChannel): Promise<UpdateReleaseInfo[]>;
    onState(cb: (state: UpdateState) => void): () => void;
  };
  maintenance: {
    scanReset(): Promise<ResetScope[]>;
    purgeReset(scopes: string[]): Promise<ResetReport>;
    chooseDataDirectory(): Promise<DataDirectorySelection | null>;
    relocateDataDirectory(authorization: string): Promise<DataRelocationResult>;
    onRelocationProgress(cb: (stage: DataRelocationStage) => void): () => void;
    scanUninstall(): Promise<UninstallTarget[]>;
    purgeUninstall(includeModels: boolean): Promise<{ scheduled: true; size_bytes: number }>;
  };
  watch: {
    pick(): Promise<WatchSelection | null>;
    scan(token: string): Promise<WatchEntry[]>;
    enqueue(request: WatchUpload): Promise<void>;
    stop(token: string): Promise<void>;
  };
  app: {
    /** From frontend/package.json via __APP_VERSION__ / extraMetadata. */
    version: string;
    platform: NodeJS.Platform;
    isDev: boolean;
    onNavigate(callback: (path: string) => void): () => void;
    onPersistenceFlush(callback: () => void | Promise<void>): () => void;
  };
  backend: {
    getStatus(): Promise<BackendStatus>;
    acknowledgeCrash(): Promise<NativeCrashRecord | undefined>;
    /** Subscribe to status changes. Returns an unsubscribe function. */
    onStatus(cb: (status: BackendStatus) => void): () => void;
    /** Kill (if managed) and relaunch the backend. */
    restart(): Promise<void>;
    setupRuntime(): Promise<void>;
    cleanSetupRuntime(): Promise<void>;
    setRuntimeRegion(region: RuntimeRegion): Promise<RuntimeRegion>;
    chooseRuntimeLocation(title: string): Promise<RuntimeLocation | null>;
    useDefaultRuntimeLocation(): Promise<RuntimeLocation>;
    getConnection(): Promise<BackendConnection>;
    testRemote(input: { url: string; apiKey: string }): Promise<RemoteBackendProbe>;
    useRemote(input: { url: string; apiKey: string }): Promise<RemoteBackendProbe>;
    useLocal(): Promise<BackendConnection>;
    websocketUrl(path: '/ws/transcribe' | '/ws/events' | '/ws/tts'): Promise<string>;
  };
  capture: {
    phase(
      session: number,
      phase: 'starting' | 'recording' | 'transcribing' | 'done' | 'error',
    ): Promise<void>;
    getShortcut(): Promise<{
      accelerator: string;
      active: boolean;
      error?: string;
    }>;
    /** Read-only notification from an enabled native shortcut press, never a tray action. */
    onShortcutPressed(callback: (accelerator: string) => void): () => void;
    setShortcut(
      accelerator: string,
    ): Promise<{ accelerator: string; active: boolean; error?: string }>;
    syncPreferences(prefs: {
      enabled: boolean;
      mode: 'hold' | 'toggle';
    }): Promise<{ accelerator: string; active: boolean; error?: string }>;
    ready(): Promise<void>;
    accept(session: number): Promise<void>;
    deliver(session: number, sequence: number, text: string): Promise<'inserted' | 'copied'>;
    finish(session: number): Promise<void>;
    cancel(session: number | null): Promise<void>;
    stop(): Promise<void>;
    labels(labels: {
      show: string;
      start: string;
      stop: string;
      settings: string;
      exit: string;
    }): Promise<void>;
    onEvent(
      callback: (event: { session: number; action: 'start' | 'stop' | 'cancel' }) => void,
    ): () => void;
  };
  files: {
    authorizeMediaTool(kind: 'ffmpeg' | 'ffprobe'): Promise<AuthorizedPathSelection | null>;
    /** Authorize a chosen model cache directory, or the platform default when reset is true. */
    authorizeModelsDirectory(reset?: boolean): Promise<AuthorizedPathSelection | null>;
    extractScript(input: { name: string; data: Uint8Array }): Promise<string>;
    /** Native "Save as…" for a generated take: main fetches the URL and writes the file. */
    saveAudio(req: SaveAudioRequest): Promise<SaveAudioResult>;
    saveData(req: SaveDataRequest): Promise<SaveAudioResult>;
    /** Reveal an absolute path in Finder / Explorer / file manager. */
    revealPath(path: string): Promise<void>;
    /** Open an absolute path or URL with the OS default handler. */
    openExternal(url: string): Promise<void>;
  };
  window: {
    minimize(): void;
    toggleMaximize(): void;
    close(): void;
    onMaximizedChange(cb: (maximized: boolean) => void): () => void;
    isMaximized(): Promise<boolean>;
  };
}

declare global {
  interface Window {
    voicestudio: VoiceStudioBridge;
  }
}

export {};
