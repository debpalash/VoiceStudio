#!/usr/bin/env node
// Build-time helper for flake.nix.
//
// The packaged Electron app only loads a handful of packages from
// node_modules at runtime: everything the main and preload bundles import
// without bundling it, plus the module the .docx worker resolves by name.
// The renderer is fully bundled, so its dependencies never need to ship.
//
// bun installs with an isolated layout:
//
//   node_modules/.bun/<pkg>@<version>/node_modules/<pkg>
//   node_modules/.bun/<pkg>@<version>/node_modules/<dep> -> ../../<dep>@<v>/...
//
// This script walks that layout from the runtime roots, copies only the
// entries that are reachable, and recreates the top-level symlinks. The
// result is relocatable and contains no reference to the workspace tree.
//
// Usage: prune-runtime-node-modules.mjs <workspace-root> <out-dir>

import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const roots = [
  '@electron-toolkit/utils',
  'electron-updater',
  'proxy-agent',
  'word-extractor',
];

// The Electron runtime provides its own `electron` module; shipping the npm
// package that bun linked for the peer dependency would shadow nothing useful
// and drags @electron/get and friends along.
const skip = new Set(['electron']);

const [sourceRoot, outDir] = process.argv.slice(2);
if (!sourceRoot || !outDir) {
  console.error('usage: prune-runtime-node-modules.mjs <workspace-root> <out-dir>');
  process.exit(2);
}

const sourceModules = fs.realpathSync(path.join(sourceRoot, 'node_modules'));
const bunStore = path.join(sourceModules, '.bun');
const requireFromElectron = createRequire(path.join(sourceRoot, 'electron', 'package.json'));

/** bun store entry name owning `dir`, or null when `dir` is outside it. */
function bunEntry(dir) {
  const rel = path.relative(bunStore, dir);
  if (rel.startsWith('..') || path.isAbsolute(rel)) return null;
  return rel.split(path.sep)[0];
}

/** The node_modules directory that owns an installed package directory. */
function ownerModules(dir) {
  let current = dir;
  while (path.basename(current) !== 'node_modules') {
    const parent = path.dirname(current);
    if (parent === current) throw new Error(`no node_modules above ${dir}`);
    current = parent;
  }
  return current;
}

const entries = new Map(); // bun entry -> real package directory
const names = new Map(); // package name -> bun entry

function addPackage(dir) {
  const entry = bunEntry(dir);
  if (!entry) return;
  if (entries.has(entry)) return;

  const manifest = path.join(dir, 'package.json');
  if (!fs.existsSync(manifest)) return;

  const name = JSON.parse(fs.readFileSync(manifest, 'utf8')).name;
  if (skip.has(name)) return;
  entries.set(entry, dir);
  names.set(name, entry);

  const modules = ownerModules(dir);
  for (const child of fs.readdirSync(modules, { withFileTypes: true })) {
    if (child.name === '.bin') continue;
    const childPath = path.join(modules, child.name);
    const candidates = child.name.startsWith('@')
      ? fs.readdirSync(childPath).map((scoped) => path.join(childPath, scoped))
      : [childPath];
    for (const candidate of candidates) {
      let real;
      try {
        real = fs.realpathSync(candidate);
      } catch {
        continue;
      }
      addPackage(real);
    }
  }
}

/** Package root for a resolved module entry. */
function packageRoot(entryPath) {
  let dir = path.dirname(entryPath);
  while (!fs.existsSync(path.join(dir, 'package.json'))) {
    const parent = path.dirname(dir);
    if (parent === dir) throw new Error(`no package.json above ${entryPath}`);
    dir = parent;
  }
  return fs.realpathSync(dir);
}

for (const spec of roots) {
  addPackage(packageRoot(requireFromElectron.resolve(spec)));
}

fs.rmSync(outDir, { recursive: true, force: true });
fs.mkdirSync(path.join(outDir, '.bun'), { recursive: true });

for (const entry of entries.keys()) {
  fs.cpSync(path.join(bunStore, entry), path.join(outDir, '.bun', entry), {
    recursive: true,
    verbatimSymlinks: true,
  });
}

for (const [name, entry] of names) {
  const link = path.join(outDir, name);
  const target = path.join(outDir, '.bun', entry, 'node_modules', name);
  fs.mkdirSync(path.dirname(link), { recursive: true });
  fs.symlinkSync(path.relative(path.dirname(link), target), link);
}

// bun links each package's peer dependencies inside its store entry, so a
// package that peers on a skipped one (@electron-toolkit/utils on electron)
// keeps a link to an entry that is not shipped. Drop exactly those; any other
// dangling link means the walk above missed a package, which must fail here.
function pointsAtSkipped(target) {
  const parts = target.split(/[\\/]/);
  return [...skip].some((name) => parts.includes(name));
}

function dropSkippedLinks(dir) {
  for (const child of fs.readdirSync(dir, { withFileTypes: true })) {
    const childPath = path.join(dir, child.name);
    if (child.isSymbolicLink()) {
      if (fs.existsSync(childPath)) continue;
      const target = fs.readlinkSync(childPath);
      if (!pointsAtSkipped(target)) {
        throw new Error(`dangling symlink in the runtime node_modules: ${childPath} -> ${target}`);
      }
      fs.unlinkSync(childPath);
    } else if (child.isDirectory()) {
      dropSkippedLinks(childPath);
    }
  }
}

dropSkippedLinks(outDir);

console.log(
  `pruned runtime node_modules: ${entries.size} packages, ${names.size} top-level entries`,
);
