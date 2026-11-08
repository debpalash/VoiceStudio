import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { renameSyncWithRetry } from '../rename-retry';

/** Owner rule 2026-10-16: off by default; skipping setup means off; sync implies account. */
export interface CloudSwitches {
  accountEnabled: boolean;
  syncEnabled: boolean;
  prompted: boolean;
}

const OFF: CloudSwitches = { accountEnabled: false, syncEnabled: false, prompted: false };

function normalize(raw: Partial<CloudSwitches>): CloudSwitches {
  const accountEnabled = raw.accountEnabled === true;
  return {
    accountEnabled,
    syncEnabled: accountEnabled && raw.syncEnabled === true,
    prompted: raw.prompted === true,
  };
}

export class CloudSwitchStore {
  private value: CloudSwitches;
  private readonly listeners = new Set<(s: CloudSwitches, prev: CloudSwitches) => void>();

  constructor(private readonly path: string) {
    let raw: Partial<CloudSwitches> = OFF;
    try {
      raw = JSON.parse(readFileSync(path, 'utf8'));
    } catch {
      /* first run or corrupt: everything off */
    }
    this.value = normalize(raw ?? OFF);
  }

  get(): CloudSwitches {
    return { ...this.value };
  }

  set(patch: Partial<CloudSwitches>): CloudSwitches {
    const prev = this.value;
    this.value = normalize({ ...prev, ...patch });
    const temporary = `${this.path}.${process.pid}.tmp`;
    try {
      mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 });
      writeFileSync(temporary, JSON.stringify(this.value), { encoding: 'utf8', mode: 0o600 });
      renameSyncWithRetry(temporary, this.path);
    } catch (error) {
      rmSync(temporary, { force: true });
      throw error;
    } finally {
      // "Off" must take effect now even when it cannot be saved (disk full).
      for (const cb of this.listeners) cb(this.get(), { ...prev });
    }
    return this.get();
  }

  subscribe(cb: (s: CloudSwitches, prev: CloudSwitches) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }
}
