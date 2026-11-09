import { spawnSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import {
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

// Keep coordination outside the bundle cache: a valid, read-only cache can
// still launch. Canonical paths give worktrees sharing node_modules one lock.
export function macDevLeaseDirectory(cacheRoot) {
  const key = createHash('sha256').update(realpathSync(cacheRoot)).digest('hex');
  return join(homedir(), 'Library', 'Caches', 'VoiceStudio', 'dev-bundle-locks', key);
}

// /usr/bin/lockf first shipped in macOS 15; older releases lack it but always
// ship /usr/bin/perl, whose flock() takes the same open-file-description lock.
const PERL_FLOCK =
  'use Fcntl qw(:flock); open(my $fh, "+<&=", 3) or die "fd 3: $!\\n";' +
  ' exit(flock($fh, LOCK_EX | ($ARGV[0] ? 0 : LOCK_NB)) ? 0 : 75);';

export function lockCommand(wait, exists = existsSync) {
  return exists('/usr/bin/lockf')
    ? ['/usr/bin/lockf', ['-s', ...(wait ? [] : ['-t', '0']), '3']]
    : ['/usr/bin/perl', ['-e', PERL_FLOCK, wait ? '1' : '0']];
}

function lockDescriptor(fd, wait) {
  // Both helpers lock the inherited descriptor 3. The kernel lock survives
  // this helper's exit until every copy of that open descriptor is closed.
  const [command, args] = lockCommand(wait);
  const result = spawnSync(command, args, {
    stdio: ['ignore', 'pipe', 'pipe', fd],
    encoding: 'utf8',
  });
  if (result.status === 0) return true;
  if (!wait && result.status === 75) return false; // EX_TEMPFAIL: another holder
  throw new Error(
    `Could not coordinate macOS development bundles: ${result.error?.message || result.stderr?.trim() || result.status}`,
  );
}

export function withMacDevCacheLock(cacheRoot, action, leaseDirectory) {
  mkdirSync(cacheRoot, { recursive: true });
  const directory = leaseDirectory ?? macDevLeaseDirectory(cacheRoot);
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const fd = openSync(join(directory, 'coordination.lock'), 'a+', 0o600);
  try {
    lockDescriptor(fd, true);
    return action(directory);
  } finally {
    closeSync(fd);
  }
}

/** Call under the coordination lock, before exposing a prepared executable. */
export function holdMacDevBundle(directory, bundleRoot) {
  const path = join(directory, `lease-${randomUUID()}.json`);
  const fd = openSync(path, 'wx+', 0o600);
  try {
    writeFileSync(fd, JSON.stringify({ bundleRoot: realpathSync(bundleRoot) }));
    lockDescriptor(fd, true);
  } catch (error) {
    closeSync(fd);
    rmSync(path, { force: true });
    throw error;
  }
  let closed = false;
  return {
    fd,
    release() {
      if (closed) return;
      closed = true;
      closeSync(fd);
      // Do not unlink: electron-vite may still hold the inherited descriptor.
      // A later coordinated prune reclaims this record once all holders exit.
    },
  };
}

/** null means usage could not be verified; in that case pruning must stop. */
export function activeMacDevBundles(directory) {
  const roots = new Set();
  let entries;
  try {
    entries = readdirSync(directory);
  } catch (error) {
    return error.code === 'ENOENT' ? roots : null;
  }
  try {
    for (const name of entries.filter((entry) => /^lease-[\w-]+\.json$/.test(entry))) {
      const path = join(directory, name);
      const fd = openSync(path, 'r');
      let inactive;
      try {
        inactive = lockDescriptor(fd, false);
        if (!inactive) {
          const { bundleRoot } = JSON.parse(readFileSync(fd, 'utf8'));
          if (typeof bundleRoot !== 'string') return null;
          roots.add(bundleRoot);
        }
      } finally {
        closeSync(fd);
      }
      if (inactive) {
        try {
          rmSync(path, { force: true });
        } catch {
          /* Best-effort metadata cleanup. */
        }
      }
    }
  } catch {
    return null;
  }
  return roots;
}
