import { execFileSync } from 'node:child_process';

const SHA = /^[0-9a-f]{7,40}$/i;

/**
 * The commit a build was made from, as a 9-character prefix, for bug reports: a
 * version string alone cannot tell two preview or beta builds of the same version
 * apart. `VOICESTUDIO_BUILD_COMMIT` wins, then CI's `GITHUB_SHA`, then the local
 * checkout; a source archive with no git history reports "unknown". Anything that
 * is not a hex commit id is rejected so a stray variable can never end up in a report.
 */
export function buildCommit(env = process.env, git = readGitHead) {
  for (const candidate of [env.VOICESTUDIO_BUILD_COMMIT, env.GITHUB_SHA, git()]) {
    const value = String(candidate ?? '').trim();
    if (SHA.test(value)) return value.slice(0, 9).toLowerCase();
  }
  return 'unknown';
}

function readGitHead() {
  try {
    return execFileSync('git', ['rev-parse', 'HEAD'], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 5000,
    });
  } catch {
    return '';
  }
}
