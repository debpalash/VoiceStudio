#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import process from 'node:process';

function run(command, args) {
  console.log(`\n> ${command} ${args.join(' ')}`);
  const result = spawnSync(command, args, {
    cwd: process.cwd(),
    env: process.env,
    stdio: 'inherit',
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

const args = process.argv.slice(2);

if (!args.includes('--skip-build')) {
  run('bun', ['run', '--cwd', 'electron', 'dist:dir']);
}

const testArgs = args.filter((arg) => arg !== '--skip-build');
const smoke = ['electron/tests/packaged-smoke.mjs', ...(testArgs.length ? testArgs : ['--setup'])];
const headlessLinux =
  process.platform === 'linux' && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY;

if (headlessLinux) {
  const probe = spawnSync('xvfb-run', ['--help'], { stdio: 'ignore' });
  if (probe.error) {
    console.error('A display is required. Install xvfb or run this command from a desktop session.');
    process.exit(1);
  }
  run('xvfb-run', ['-a', 'node', ...smoke]);
} else {
  run('node', smoke);
}
