import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { renameSyncWithRetry } from '../rename-retry';

/**
 * The profile picture, cached on disk by `avatar_artifact_id` (vssaas
 * desktop-account-client: avatar_url always expires, so download once and fetch
 * again only when the ID changes or is cleared). Holds the picture only; the
 * short-lived download link is never stored.
 */
export class AvatarCache {
  private memory: { artifactId: string; dataUrl: string } | null | undefined;

  constructor(private readonly path: string) {}

  get(artifactId: string): string | null {
    const entry = this.read();
    return entry && entry.artifactId === artifactId ? entry.dataUrl : null;
  }

  set(artifactId: string, dataUrl: string): void {
    this.memory = { artifactId, dataUrl };
    try {
      mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 });
      const temporary = `${this.path}.${process.pid}.tmp`;
      writeFileSync(temporary, JSON.stringify({ v: 1, artifactId, dataUrl }), {
        encoding: 'utf8',
        mode: 0o600,
      });
      renameSyncWithRetry(temporary, this.path);
    } catch {
      /* the in-memory copy still serves this run */
    }
  }

  clear(): void {
    this.memory = null;
    rmSync(this.path, { force: true });
  }

  private read(): { artifactId: string; dataUrl: string } | null {
    if (this.memory !== undefined) return this.memory;
    try {
      const { v, artifactId, dataUrl } = JSON.parse(readFileSync(this.path, 'utf8'));
      this.memory =
        v === 1 &&
        typeof artifactId === 'string' &&
        typeof dataUrl === 'string' &&
        dataUrl.startsWith('data:image/')
          ? { artifactId, dataUrl }
          : null;
    } catch {
      this.memory = null;
    }
    return this.memory;
  }
}
