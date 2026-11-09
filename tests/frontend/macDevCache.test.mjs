import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  activeMacDevBundles,
  holdMacDevBundle,
  lockCommand,
  withMacDevCacheLock,
} from "../../electron/scripts/mac-dev-cache.mjs";

test("lock helper falls back to perl where /usr/bin/lockf is missing (macOS 14 and older)", () => {
  assert.equal(lockCommand(true, () => true)[0], "/usr/bin/lockf");
  const [command, args] = lockCommand(false, () => false);
  assert.equal(command, "/usr/bin/perl");
  assert.equal(args.at(-1), "0");
  assert.equal(lockCommand(true, () => false)[1].at(-1), "1");
});

test("leases stay held while open and are pruned once released", { skip: process.platform !== "darwin" }, () => {
  const root = mkdtempSync(join(tmpdir(), "mac-dev-cache-"));
  try {
    const cache = join(root, "cache");
    const leases = join(root, "leases");
    const lease = withMacDevCacheLock(cache, (directory) => holdMacDevBundle(directory, cache), leases);
    assert.equal(activeMacDevBundles(leases)?.size, 1);
    lease.release();
    assert.equal(activeMacDevBundles(leases)?.size, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
