// Copy the uv on PATH to electron/build/uv/, where electron-builder.config.mjs
// picks it up as the installer's bundled uv. Run from the repository root with
// VOICESTUDIO_RUST_TARGET set to the package target; a runner of another OS or
// architecture fails here instead of shipping the wrong binary.
// Used by the release, the packaging rehearsal and the PR release smoke.
import { execFileSync } from 'node:child_process';
import { mkdirSync, copyFileSync, chmodSync } from 'node:fs';
import { join } from 'node:path';

const expected = process.env.VOICESTUDIO_RUST_TARGET;
const targets = {
  'linux-x64': 'x86_64-unknown-linux-gnu',
  'win32-x64': 'x86_64-pc-windows-msvc',
  'win32-arm64': 'aarch64-pc-windows-msvc',
  'darwin-arm64': 'aarch64-apple-darwin',
  'darwin-x64': 'x86_64-apple-darwin',
};
if (targets[`${process.platform}-${process.arch}`] !== expected)
  throw new Error('Runner architecture does not match package target');
const source = execFileSync(process.platform === 'win32' ? 'where.exe' : 'which', ['uv'], {
  encoding: 'utf8',
})
  .trim()
  .split(/\r?\n/)[0];
const dir = 'electron/build/uv';
mkdirSync(dir, { recursive: true });
const destination = join(dir, `uv-${expected}${process.platform === 'win32' ? '.exe' : ''}`);
copyFileSync(source, destination);
if (process.platform !== 'win32') chmodSync(destination, 0o755);
