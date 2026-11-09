import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { renameSyncWithRetry } from '../rename-retry';

export interface SafeStorageLike {
  isEncryptionAvailable(): boolean;
  getSelectedStorageBackend?(): string;
  encryptString(plain: string): Buffer;
  decryptString(encrypted: Buffer): string;
}

/**
 * The refresh token lives only in Electron main; never as plain text on disk.
 *
 * safeStorage is consulted only when there is a saved token to read or a new one
 * to write. On macOS asking it opens the Keychain, and a re-signed development
 * bundle gets an access prompt that blocks the main process before the backend
 * starts — on every launch, even with account features off (#3813).
 */
export class RefreshTokenStore {
  private encryption: boolean | null = null;
  private memory: string | null = null;
  private identity: string | null = null;
  private loaded = false;

  constructor(
    private readonly path: string,
    private readonly safe: SafeStorageLike,
    private readonly platform: NodeJS.Platform = process.platform,
  ) {}

  /** Whether the token survives a restart. The first read asks the OS keyring. */
  get persistent(): boolean {
    if (this.encryption === null) {
      try {
        this.encryption =
          this.safe.isEncryptionAvailable() &&
          !(this.platform === 'linux' && this.safe.getSelectedStorageBackend?.() === 'basic_text');
      } catch {
        this.encryption = false; // A locked or broken keyring keeps the session in memory only.
      }
    }
    return this.encryption;
  }

  load(): string | null {
    if (this.loaded) return this.memory;
    // Nothing saved: no keyring access (this process writes the file itself on sign-in).
    if (!existsSync(this.path)) {
      this.loaded = true;
      return this.memory;
    }
    if (!this.persistent) return this.memory;
    this.loaded = true;
    try {
      const { v, token } = JSON.parse(readFileSync(this.path, 'utf8'));
      if ((v !== 1 && v !== 2) || typeof token !== 'string') return null;
      const decrypted = this.safe.decryptString(Buffer.from(token, 'base64'));
      if (v === 1) {
        this.memory = decrypted || null;
      } else {
        const record = JSON.parse(decrypted);
        if (typeof record.refreshToken !== 'string' || !record.refreshToken) return null;
        this.memory = record.refreshToken;
        this.identity =
          typeof record.accountId === 'string' && /^[A-Za-z0-9]{16}$/.test(record.accountId)
            ? record.accountId
            : null;
      }
    } catch {
      this.memory = null;
      this.identity = null;
    }
    return this.memory;
  }

  /** Cached authenticated identity: no token refresh or network request. */
  accountId(): string | null {
    return this.load() ? this.identity : null;
  }

  bindAccount(accountId: string): boolean {
    if (!/^[A-Za-z0-9]{16}$/.test(accountId) || !this.load()) return false;
    // A live session cannot be rebound to a different person by a later response.
    if (this.identity && this.identity !== accountId) return false;
    this.identity = accountId;
    this.save(this.memory!);
    return true;
  }

  save(token: string, options: { newSession?: boolean } = {}): void {
    this.load();
    if (options.newSession) this.identity = null;
    this.memory = token;
    if (!this.persistent) return;
    mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.${process.pid}.tmp`;
    const record = JSON.stringify({ refreshToken: token, accountId: this.identity });
    const body = { v: 2, token: this.safe.encryptString(record).toString('base64') };
    writeFileSync(temporary, JSON.stringify(body), { encoding: 'utf8', mode: 0o600 });
    renameSyncWithRetry(temporary, this.path);
  }

  clear(): void {
    this.loaded = true;
    this.memory = null;
    this.identity = null;
    rmSync(this.path, { force: true });
  }
}
