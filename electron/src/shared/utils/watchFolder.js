/**
 * Watch-folder ingest for the batch dubbing queue (opt-in, local-only).
 *
 * A watch source hands back directory listings and upload candidates; the UI
 * (WatchFolderBar) polls it every ~5s and pushes NEW videos through the same
 * `enqueueBatchJob` API as the Add-to-queue dialog. Two backends:
 *
 *   - Tauri (desktop, all three OSes): a native folder pick registers the
 *     directory under a session token in the Rust process
 *     (`src-tauri/src/watch_folder.rs`); scans and native streamed uploads
 *     resolve the token. Filesystem paths NEVER ride an HTTP request — the
 *     backend only receives multipart bytes.
 *   - Browsers with the File System Access API (Chromium): a directory
 *     handle from `showDirectoryPicker()` is polled directly.
 *
 * Everything else (Firefox/Safari web builds) throws `watch-unsupported`,
 * which the UI turns into an actionable message.
 */
import { isTauriContext } from './apiBase';
import { API } from '../api/client';

/** Poll cadence — no native fs-watch plugin ships in this repo, so both
 *  backends rescan on a timer. 5s keeps ingest snappy without disk churn. */
export const WATCH_POLL_MS = 5000;

// Container formats the dub pipeline's ffmpeg extract stage accepts. Watch
// entries carry no MIME type, so filtering is by extension (lowercased).
const VIDEO_EXTENSIONS = new Set(['mp4', 'm4v', 'mov', 'mkv', 'webm', 'avi', 'mpg', 'mpeg', 'wmv']);

const MIME_BY_EXTENSION = {
  mp4: 'video/mp4',
  m4v: 'video/mp4',
  mov: 'video/quicktime',
  mkv: 'video/x-matroska',
  webm: 'video/webm',
  avi: 'video/x-msvideo',
  mpg: 'video/mpeg',
  mpeg: 'video/mpeg',
  wmv: 'video/x-ms-wmv',
};

function extensionOf(name) {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : '';
}

/** True when a bare filename looks like a video the batch pipeline can dub. */
export function isVideoFile(name) {
  return VIDEO_EXTENSIONS.has(extensionOf(typeof name === 'string' ? name : ''));
}

export function videoMimeFor(name) {
  return MIME_BY_EXTENSION[extensionOf(name)] || 'application/octet-stream';
}

/** Dedup identity: name + size + mtime. A re-listed unchanged file is the
 *  same key (skipped); a rewritten/renamed file is a new key (re-ingested). */
export function entryKey(entry) {
  return `${entry.name}\u0000${entry.size}\u0000${entry.mtime}`;
}

/**
 * Tracks which directory entries have already been ingested (or predate the
 * watch) and which are still settling.
 *
 * - `prime(entries)` marks everything currently in the folder as seen —
 *   starting a watch must not enqueue the folder's existing contents.
 * - `next(entries)` returns the video entries that are new AND stable: a
 *   candidate is only released once two consecutive scans agree on its
 *   name+size+mtime, so a large file still being copied in (size/mtime moving
 *   between polls) is never uploaded half-written.
 */
export function createIngestTracker() {
  const seen = new Set();
  const pending = new Map(); // name → key awaiting a confirming rescan

  return {
    prime(entries) {
      for (const entry of entries) seen.add(entryKey(entry));
    },
    next(entries) {
      const ready = [];
      const present = new Set();
      for (const entry of entries) {
        if (!isVideoFile(entry.name)) continue;
        present.add(entry.name);
        const key = entryKey(entry);
        if (seen.has(key)) {
          pending.delete(entry.name);
          continue;
        }
        if (pending.get(entry.name) === key) {
          seen.add(key);
          pending.delete(entry.name);
          ready.push(entry);
        } else {
          pending.set(entry.name, key); // new or still changing — wait a poll
        }
      }
      for (const name of pending.keys()) {
        if (!present.has(name)) pending.delete(name); // vanished mid-copy
      }
      return ready;
    },
    /** Hand a released entry back untouched (the watcher was paused or torn
     *  down mid-poll before it could enqueue): the file is known-stable, so
     *  it re-releases on the very next unpaused scan. */
    unsee(entry) {
      const key = entryKey(entry);
      seen.delete(key);
      pending.set(entry.name, key);
    },
    retry(entry) {
      // `next` reserves a stable entry before the asynchronous read/upload.
      // Release that reservation after a transient failure so a later poll
      // can settle and try the same file again (from scratch — unlike
      // `unsee`, the failure may mean the file is changing again).
      seen.delete(entryKey(entry));
      pending.delete(entry.name);
    },
  };
}

async function openTauriSource() {
  const { invoke } = await import('@tauri-apps/api/core');
  const selection = await invoke('watch_folder_pick');
  if (!selection) return null; // user cancelled the native picker
  const { token, path } = selection;
  // Display label only — the path never leaves the app over HTTP.
  const label = path.split(/[\\/]/).filter(Boolean).pop() || path;
  return {
    label,
    path,
    listEntries: () => invoke('watch_folder_scan', { token }),
    // A lightweight capability descriptor, not file bytes. The batch API
    // client hands this back to Rust, which streams the pinned file handle
    // directly to the loopback backend without retaining it in renderer RAM.
    async readFile(entry) {
      return {
        __voiceStudioNativeWatchUpload: true,
        token,
        name: entry.name,
        size: entry.size,
        mtime: entry.mtime,
        type: videoMimeFor(entry.name),
        lastModified: entry.mtime,
      };
    },
    close() {
      invoke('watch_folder_forget', { token }).catch(() => {});
    },
  };
}

async function openBrowserSource() {
  let handle;
  try {
    handle = await window.showDirectoryPicker({ mode: 'read' });
  } catch (e) {
    if (e?.name === 'AbortError') return null; // user cancelled the picker
    throw e;
  }
  return {
    label: handle.name,
    path: handle.name,
    async listEntries() {
      const entries = [];
      for await (const item of handle.values()) {
        if (item.kind !== 'file') continue;
        const file = await item.getFile();
        entries.push({ name: file.name, size: file.size, mtime: file.lastModified });
      }
      return entries;
    },
    async readFile(entry) {
      const fileHandle = await handle.getFileHandle(entry.name);
      const file = await fileHandle.getFile();
      // Bind the read to the settled scan snapshot — a file replaced between
      // settling and reading is refused; its new content re-settles on later
      // scans and is ingested then.
      if (file.size !== entry.size || file.lastModified !== entry.mtime) {
        throw new Error(`Watched file ${entry.name} changed after it was scanned`);
      }
      return file;
    },
    close() {},
  };
}

/**
 * Ask the user for a folder and return a watch source, or `null` on cancel.
 * Throws an Error with `code === 'watch-unsupported'` where no local folder
 * access exists (web build outside Chromium).
 */
export async function openWatchSource() {
  if (isTauriContext()) {
    let localApi = false;
    try {
      const endpoint = new URL(API);
      localApi =
        endpoint.protocol === 'http:' &&
        ['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname) &&
        (endpoint.pathname === '/' || endpoint.pathname === '');
    } catch {
      /* invalid configured endpoint is not a safe native upload target */
    }
    if (!localApi) {
      const err = new Error('Native folder watching requires the local backend');
      err.code = 'watch-unsupported';
      throw err;
    }
    return openTauriSource();
  }
  if (typeof window !== 'undefined' && typeof window.showDirectoryPicker === 'function') {
    return openBrowserSource();
  }
  const err = new Error('Folder watching is unavailable in this browser');
  err.code = 'watch-unsupported';
  throw err;
}
