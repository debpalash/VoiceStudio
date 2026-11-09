import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, mkdirSync, rmSync, statSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import test from 'node:test';

import metadata from '../../store/metadata.json' with { type: 'json' };
import {
  repairSnapTemplatePayload,
  validateTemplateArchiveEntries,
  verifySnapRuntimePayload,
} from '../snap-template-payload.mjs';
import { verifySnapMetadata } from '../../scripts/verify_snap_artifact.mjs';

const snapYaml = `base: core22
grade: stable
confinement: strict
plugs:
  browser-support:
    interface: browser-support
    allow-sandbox: false
name: voicestudio
version: 0.5.7
title: VoiceStudio
summary: ${metadata.listing.shortDescription}
description: ${metadata.listing.description}
apps:
  voicestudio:
    plugs:
      - browser-support
      - desktop
      - desktop-legacy
      - home
      - x11
      - wayland
      - unity7
      - network
      - network-bind
      - gsettings
      - audio-playback
      - audio-record
      - pulseaudio
      - opengl
`;

test('accepts canonical strict Snap metadata', () => {
  assert.deepEqual(verifySnapMetadata(snapYaml, { ...metadata, version: '0.5.7' }), {
    name: 'voicestudio',
    version: '0.5.7',
    confinement: 'strict',
    base: 'core22',
    plugs: [
      'browser-support',
      'desktop',
      'desktop-legacy',
      'home',
      'x11',
      'wayland',
      'unity7',
      'network',
      'network-bind',
      'gsettings',
      'audio-playback',
      'audio-record',
      'pulseaudio',
      'opengl',
    ],
  });
});

test('rejects weakened confinement, missing capabilities and broad removable access', () => {
  const expected = { ...metadata, version: '0.5.7' };
  assert.throws(() => verifySnapMetadata(snapYaml.replace('strict', 'classic'), expected));
  assert.throws(() => verifySnapMetadata(snapYaml.replace('allow-sandbox: false', 'allow-sandbox: true'), expected));
  assert.throws(() => verifySnapMetadata(snapYaml.replace('    allow-sandbox: false\n', ''), expected));
  assert.throws(() => verifySnapMetadata(snapYaml.replace('      - audio-record\n', ''), expected));
  assert.throws(() => verifySnapMetadata(`${snapYaml}      - removable-media\n`, expected));
});

const runtimeListing = `
squashfs-root/desktop-init.sh
squashfs-root/desktop-common.sh
squashfs-root/desktop-gnome-specific.sh
squashfs-root/usr/lib/x86_64-linux-gnu/libnss3.so
`;

test('requires an expanded desktop runtime in the Snap payload', () => {
  assert.doesNotThrow(() => verifySnapRuntimePayload(runtimeListing));
  assert.throws(
    () => verifySnapRuntimePayload(runtimeListing.replace('squashfs-root/desktop-init.sh\n', '')),
    /desktop-init\.sh/,
  );
  assert.throws(
    () =>
      verifySnapRuntimePayload(
        `${runtimeListing}squashfs-root/snap-template-electron-4.0-2-amd64.tar\n`,
      ),
    /unexpanded desktop runtime/,
  );
});

test('rejects unsafe or incomplete nested template archives', () => {
  const entries = `./\n${runtimeListing.replaceAll('squashfs-root/', './')}`;
  assert.doesNotThrow(() => validateTemplateArchiveEntries(entries));
  assert.throws(() => validateTemplateArchiveEntries(`${entries}../escape\n`), /Unsafe/);
  assert.throws(
    () => validateTemplateArchiveEntries(entries.replace('./desktop-init.sh\n', '')),
    /desktop-init\.sh/,
  );
});

const hasSquashfsTools =
  spawnSync('mksquashfs', ['-version']).error === undefined &&
  spawnSync('unsquashfs', ['-version']).error === undefined;

for (const nested of [true, false]) test(
  `configures strict-confinement startup for a ${nested ? 'nested' : 'expanded'} Snap payload`,
  { skip: hasSquashfsTools ? false : 'squashfs-tools is not installed' },
  () => {
    const workDir = mkdtempSync(resolve(tmpdir(), 'voicestudio-snap-repair-test-'));
    const source = resolve(workDir, 'source');
    const template = resolve(workDir, 'template');
    const artifact = resolve(workDir, 'VoiceStudio.snap');
    const repaired = resolve(workDir, 'repaired');
    try {
      mkdirSync(source);
      mkdirSync(template);
      writeFileSync(resolve(source, 'command.sh'), '#!/bin/sh\nexec \"$SNAP/desktop-init.sh\" \"$SNAP/desktop-common.sh\" \"$SNAP/desktop-gnome-specific.sh\" \"$SNAP/voicestudio\" \"$@\"\n');
      for (const script of ['desktop-init.sh', 'desktop-common.sh', 'desktop-gnome-specific.sh']) {
        writeFileSync(resolve(template, script), '#!/bin/sh\n');
      }
      const runtimeLibrary = resolve(template, 'usr/lib/x86_64-linux-gnu/libnss3.so');
      mkdirSync(resolve(runtimeLibrary, '..'), { recursive: true });
      writeFileSync(runtimeLibrary, 'fixture');
      const nestedArchive = resolve(source, 'snap-template-electron-4.0-2-amd64.tar');
      if (nested) execFileSync('tar', ['-cf', nestedArchive, '-C', template, '.']);
      else execFileSync('cp', ['-a', `${template}/.`, source]);
      execFileSync('mksquashfs', [
        source,
        artifact,
        '-noappend',
        '-all-root',
        '-processors',
        '1',
        '-quiet',
      ]);

      assert.equal(repairSnapTemplatePayload(artifact), true);
      execFileSync('unsquashfs', ['-quiet', '-d', repaired, artifact]);

      assert.equal(existsSync(resolve(repaired, 'snap-template-electron-4.0-2-amd64.tar')), false);
      for (const path of ['desktop-init.sh', 'desktop-common.sh', 'desktop-gnome-specific.sh']) {
        assert.equal(statSync(resolve(repaired, path)).mode & 0o111, 0o111);
      }
      assert.equal(existsSync(resolve(repaired, 'usr/lib/x86_64-linux-gnu/libnss3.so')), true);
      assert.match(readFileSync(resolve(repaired, 'command.sh'), 'utf8'), /--no-sandbox/);
      assert.equal(statSync(resolve(repaired, 'command.sh')).mode & 0o111, 0o111);
      assert.equal(repairSnapTemplatePayload(artifact), false);
    } finally {
      rmSync(workDir, { recursive: true, force: true });
    }
  },
);
