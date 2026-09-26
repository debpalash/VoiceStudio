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
