export interface WatchEntry {
  name: string;
  size: number;
  mtime: number;
}
export const WATCH_POLL_MS: number;
export function entryKey(entry: WatchEntry): string;
export function isVideoFile(name: string): boolean;
export function videoMimeFor(name: string): string;
export function createIngestTracker(): {
  prime(entries: WatchEntry[]): void;
  next(entries: WatchEntry[]): WatchEntry[];
  unsee(entry: WatchEntry): void;
  retry(entry: WatchEntry): void;
};
