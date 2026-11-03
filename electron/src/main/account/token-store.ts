import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { renameSyncWithRetry } from '../rename-retry';

export interface SafeStorageLike {
  isEncryptionAvailable(): boolean;
  getSelectedStorageBackend?(): string;
  encryptString(plain: string): Buffer;
  decryptString(encrypted: Buffer): string;
}

/** The refresh token lives only in Electron main; never as plain text on disk. */
export class RefreshTokenStore {
  readonly persistent: boolean;
  private memory: string | null = null;

  constructor(
    private readonly path: string,
    private readonly safe: SafeStorageLike,
    platform: NodeJS.Platform = process.platform,
  ) {
    this.persistent =
      safe.isEncryptionAvailable() &&
      !(platform === 'linux' && safe.getSelectedStorageBackend?.() === 'basic_text');
  }

  load(): string | null {
    if (this.memory || !this.persistent) return this.memory;
    try {
      const { v, token } = JSON.parse(readFileSync(this.path, 'utf8'));
      if (v !== 1 || typeof token !== 'string') return null;
      this.memory = this.safe.decryptString(Buffer.from(token, 'base64'));
    } catch {
      this.memory = null;
    }
    return this.memory;
  }

  save(token: string): void {
    this.memory = token;
    if (!this.persistent) return;
    mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.${process.pid}.tmp`;
    const body = { v: 1, token: this.safe.encryptString(token).toString('base64') };
    writeFileSync(temporary, JSON.stringify(body), { encoding: 'utf8', mode: 0o600 });
    renameSyncWithRetry(temporary, this.path);
  }

  clear(): void {
    this.memory = null;
    rmSync(this.path, { force: true });
  }
}
