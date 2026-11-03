import { mkdir, open } from 'node:fs/promises';
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

  flush(): Promise<void> {
    if (this.writing) return this.writing;
    this.writing = (async () => {
      if (!this.loaded) {
        this.loaded = true;
        try {
          // Inspect and read the same open file, even if its path is replaced.
          const file = await open(this.path, 'r');
          try {
            const info = await file.stat();
            if (info.isFile() && info.size <= MAX_CHARS * 4) {
              // Also bound reads if another writer grows the opened file after stat.
              const buffer = Buffer.alloc(MAX_CHARS * 4 + 1);
              let length = 0;
              while (length < buffer.length) {
                const { bytesRead } = await file.read(
                  buffer,
                  length,
                  buffer.length - length,
                  length,
                );
                if (!bytesRead) break;
                length += bytesRead;
              }
              if (length <= MAX_CHARS * 4)
                this.text = bounded(scrubSetupText(buffer.toString('utf8', 0, length)) + '\n');
            }
          } finally {
            await file.close();
          }
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
