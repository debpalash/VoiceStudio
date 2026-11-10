// A shipped bundle must not hold a symlink that is broken or points outside it:
// it either fails signing (v0.5.8-beta.4's macOS build) or breaks on users' machines.
import { existsSync, lstatSync, readdirSync, readlinkSync, realpathSync } from 'node:fs';
import { isAbsolute, join, relative, sep } from 'node:path';

export function bundleSymlinkProblems(root) {
  const base = realpathSync(root);
  const problems = [];
  const walk = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (entry.isSymbolicLink()) {
        const name = relative(root, path);
        const target = readlinkSync(path);
        if (isAbsolute(target)) problems.push(`${name} -> ${target} (absolute)`);
        else if (!existsSync(path)) problems.push(`${name} -> ${target} (broken)`);
        else {
          const inside = relative(base, realpathSync(path));
          if (inside === '..' || inside.startsWith('..' + sep) || isAbsolute(inside))
            problems.push(`${name} -> ${target} (outside the bundle)`);
        }
      }
    }
  };
  if (lstatSync(root).isDirectory()) walk(root);
  return problems;
}

export function assertBundleSymlinks(root) {
  const problems = bundleSymlinkProblems(root);
  if (problems.length)
    throw new Error(`Bundle symlinks must stay inside ${root}:\n  ${problems.join('\n  ')}`);
}
