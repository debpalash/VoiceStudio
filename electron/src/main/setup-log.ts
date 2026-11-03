import { mkdir, readFile, stat } from 'node:fs/promises';
import { dirname } from 'node:path';
import { cleanProcessLine } from './setup-progress';
import { scrubText } from '../shared/utils/scrub';

const MAX_CHARS = 256 * 1024;
const HEAD_CHARS = 32 * 1024;

/** Keep URL credentials and signed query strings out of locally shareable logs. */
export function scrubSetupText(value: string): string {
  return scrubText(
    cleanProcessLine(value).replace(/https?:\/\/[^\s<>"']+/gi, (raw) => {
      try {
        const url = new URL(raw);
        if (url.username || url.password) {
          url.username = 'REDACTED';
          url.password = '';
        }
        if (url.search) url.search = '?REDACTED';
        return url.href;
      } catch {
        return '[invalid URL]';
      }
    }),
  );
}

function bounded(text: string): string {
  if (text.length <= MAX_CHARS) return text;
  const marker = '\n[Earlier log lines omitted to limit file size]\n';
  return text.slice(0, HEAD_CHARS) + marker + text.slice(-(MAX_CHARS - HEAD_CHARS - marker.length));
}

/** Bounded, redacted setup history, retained across retry and app restart. */
export class SetupLog {
  private text = '';
  private loaded = false;
  private queued: string[] = [];
  private writing: Promise<void> | null = null;
  private persistenceFailed = false;

  constructor(private readonly path: string) {}

  append(message: string, now = new Date()): void {
    this.queued.push(`${now.toISOString()} ${scrubSetupText(message).slice(0, 8192)}\n`);
    this.flush();
  }

  private flush(): Promise<void> {
    if (this.writing) return this.writing;
    this.writing = (async () => {
      if (!this.loaded) {
        this.loaded = true;
        try {
          // Do not read an unexpectedly large or externally replaced file into memory.
          const info = await stat(this.path);
          if (info.isFile() && info.size <= MAX_CHARS * 4)
            this.text = bounded(scrubSetupText(await readFile(this.path, 'utf8')) + '\n');
        } catch {
          /* First install, or unavailable log directory. */
        }
      }
      while (this.queued.length) {
        this.text = bounded(this.text + this.queued.splice(0).join(''));
        try {
          await mkdir(dirname(this.path), { recursive: true });
          const { replaceFile } = await import('./replace-file');
          await replaceFile(this.path, this.text);
          this.persistenceFailed = false;
        } catch {
          // Full/locked disk must not break setup or prevent exporting the in-memory log.
          this.persistenceFailed = true;
        }
      }
    })().finally(() => {
      this.writing = null;
    });
    return this.writing;
  }

  async read(): Promise<string> {
    await this.flush();
    return (
      (this.persistenceFailed
        ? '[Could not persist setup log; exporting this session from memory]\n'
        : '') + this.text
    );
  }
}
