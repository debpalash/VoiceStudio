// The build commit shown in bug reports: where it comes from and what it refuses.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildCommit } from '../../config/build-commit.mjs';

const sha = '0123456789abcdef0123456789abcdef01234567';

test('an explicit build commit wins over CI and the checkout', () => {
  const env = { VOICESTUDIO_BUILD_COMMIT: 'ABCDEF1234567', GITHUB_SHA: sha };
  assert.equal(buildCommit(env, () => 'ffffffffffff'), 'abcdef123');
});

test('CI supplies the commit when nothing overrides it', () => {
  assert.equal(buildCommit({ GITHUB_SHA: sha }, () => ''), '012345678');
});

test('a local checkout supplies it through git', () => {
  assert.equal(buildCommit({}, () => `${sha}\n`), '012345678');
});

test('no git history reports unknown', () => {
  assert.equal(buildCommit({}, () => ''), 'unknown');
});

test('values that are not commit ids are ignored, never copied into a report', () => {
  const env = { VOICESTUDIO_BUILD_COMMIT: 'main; rm -rf /', GITHUB_SHA: 'not-a-sha' };
  assert.equal(buildCommit(env, () => 'HEAD'), 'unknown');
  assert.equal(buildCommit({ VOICESTUDIO_BUILD_COMMIT: 'abc' }, () => ''), 'unknown');
});
