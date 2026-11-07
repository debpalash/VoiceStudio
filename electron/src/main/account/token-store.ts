import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

export interface SafeStorageLike {
  isEncryptionAvailable(): boolean;
  getSelectedStorageBackend?(): string;
  encryptString(plain: string): Buffer;
  decryptString(encrypted: Buffer): string;
}

/**
 * The refresh token lives only in Electron main; never as plain text on disk.
 *
 * safeStorage is only consulted once there is a token to read or write. On macOS
 * it opens the login Keychain, which can raise a blocking access prompt (always
 * for a re-signed development bundle); with account features off, startup must
 * never wait on it.
 */
export class RefreshTokenStore {
  private memory: string | null = null;
  private persistentCache: boolean | undefined;

  constructor(
    private readonly path: string,
    private readonly safe: SafeStorageLike,
    private readonly platform: NodeJS.Platform = process.platform,
  ) {}

  get persistent(): boolean {
    this.persistentCache ??=
      this.safe.isEncryptionAvailable() &&
      !(this.platform === 'linux' && this.safe.getSelectedStorageBackend?.() === 'basic_text');
    return this.persistentCache;
  }

  load(): string | null {
    if (this.memory) return this.memory;
    let raw: string;
    try {
      raw = readFileSync(this.path, 'utf8');
    } catch {
      return null; // Nothing saved: no reason to touch the Keychain.
    }
    if (!this.persistent) return null;
    try {
      const { v, token } = JSON.parse(raw);
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
    renameSync(temporary, this.path);
  }

  clear(): void {
    this.memory = null;
    rmSync(this.path, { force: true });
  }
}
