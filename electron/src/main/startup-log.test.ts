import { EventEmitter } from 'node:events';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  installStartupLogging,
  logStartup,
  scrubStartupText,
  startupLogDirs,
  startupLogPath,
} from './startup-log';

const tmp = () => mkdtempSync(join(tmpdir(), 'vs-startup-'));

describe('main entry wiring', () => {
  const src = readFileSync(join(__dirname, 'index.ts'), 'utf8');
  it('loads the startup log before every other module', () => {
    expect(src.split('\n')[0]).toContain('./startup-log');
  });
  it('logs each silent exit', () => {
    expect(src).toContain('single-instance lock');
    expect(src).toContain('--stop with no running instance');
    expect(src).toContain('initialization failed');
  });
});

describe('startup log', () => {
  it('uses a per-user log folder on every platform, never the shared temp folder', () => {
    expect(startupLogDirs('darwin', '/Users/a', {})).toEqual(['/Users/a/Library/Logs/VoiceStudio']);
    expect(startupLogDirs('linux', '/home/a', {})).toEqual(['/home/a/.local/state/VoiceStudio']);
    expect(startupLogDirs('linux', '/home/a', { XDG_STATE_HOME: '/s' })).toEqual(['/s/VoiceStudio']);
    expect(startupLogDirs('win32', 'C:\\Users\\a', { LOCALAPPDATA: 'C:\\L' })).toEqual([
      'C:\\L\\VoiceStudio\\Logs',
    ]);
    expect(startupLogDirs('linux', '', {})).toEqual([]);
  });

  it('falls back when a directory cannot be created, and gives up without one', () => {
    const dir = tmp();
    const blocker = join(dir, 'file');
    writeFileSync(blocker, 'x');
    expect(startupLogPath([join(blocker, 'sub'), dir])).toBe(join(dir, 'VoiceStudio-startup.log'));
    expect(startupLogPath([join(blocker, 'sub')])).toBeNull();
  });

  it('appends lines and never throws on an unwritable path', () => {
    const file = join(tmp(), 'log');
    logStartup('first', file);
    logStartup('second', file);
    expect(readFileSync(file, 'utf8').trim().split('\n')).toHaveLength(2);
    expect(logStartup('x', join(file, 'nope', 'x'))).toBeNull();
    expect(logStartup('third', file)).toBe(file);
  });

  it('redacts credentials and the home path before writing', () => {
    expect(
      scrubStartupText('at /Users/alice/app.js --api-key=s3cr3t HF_TOKEN=hf_abc --inspect', '/Users/alice'),
    ).toBe('at ~/app.js --api-key=[redacted] HF_TOKEN=[redacted] --inspect');
    expect(scrubStartupText('C:/Users/a/x C:\\Users\\a\\y', 'C:\\Users\\a')).toBe('~/x ~\\y');
  });

  it('starts an oversized log over instead of growing it', () => {
    const file = join(tmp(), 'startup.log');
    writeFileSync(file, 'x'.repeat(300 * 1024));
    logStartup('fresh', file);
    const text = readFileSync(file, 'utf8');
    expect(text.length).toBeLessThan(1024);
    expect(text).toContain('fresh');
  });

  it('records fatal errors, rejections and exit codes', () => {
    const file = join(tmp(), 'log');
    const proc = new EventEmitter();
    installStartupLogging(proc, file);
    proc.emit('uncaughtExceptionMonitor', new Error('boom'), 'uncaughtException');
    proc.emit('unhandledRejection', 'nope');
    proc.emit('exit', 3);
    const text = readFileSync(file, 'utf8');
    expect(text).toContain('uncaughtException: Error: boom');
    expect(text).toContain('unhandledRejection: nope');
    expect(text).toContain('process exit code=3');
  });
});
