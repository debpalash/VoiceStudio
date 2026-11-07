import * as nodeFs from 'node:fs';
import * as nodeFsPromises from 'node:fs/promises';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  asciiSafePthFiles,
  asciiSafePthFilesSync,
  asciiSafePthText,
} from '../../scripts/pth-ascii.mjs';

// One refused rename, then one refused reread: the Windows sharing violation a
// scanning antivirus causes. A heal must not mistake the unreadable target for
// one another heal already replaced (#1783).
function refuse(code: string): Error {
  return Object.assign(new Error(`${code}: simulated sharing violation`), { code });
}

function venvWithPth(): { venv: string; pth: string; project: string } {
  const venv = mkdtempSync(join(tmpdir(), 'vs-pth-retry-'));
  const sitePackages = join(venv, 'Lib', 'site-packages');
  mkdirSync(sitePackages, { recursive: true });
  const pth = join(sitePackages, '_editable_impl_omnivoice.pth');
  const project = join(venv, '张三', 'project');
  writeFileSync(pth, project, 'utf8');
  return { venv, pth, project };
}

describe('asciiSafePthFiles under a transient sharing violation (#1783)', () => {
  it('keeps retrying when the target cannot be reread (async)', async () => {
    const { venv, pth, project } = venvWithPth();
    const faults = { rename: 1, reread: 0 };
    const fs = {
      ...nodeFsPromises,
      rename: (async (...args: Parameters<typeof nodeFsPromises.rename>) => {
        if (faults.rename > 0) {
          faults.rename--;
          faults.reread++;
          throw refuse('EPERM');
        }
        return nodeFsPromises.rename(...args);
      }) as typeof nodeFsPromises.rename,
      readFile: (async (file: string) => {
        if (faults.reread > 0) {
          faults.reread--;
          throw refuse('EBUSY');
        }
        return nodeFsPromises.readFile(file);
      }) as typeof nodeFsPromises.readFile,
    };

    expect(await asciiSafePthFiles(venv, fs)).toEqual([pth]);
    expect(faults).toEqual({ rename: 0, reread: 0 });
    expect(readFileSync(pth, 'utf8')).toBe(asciiSafePthText(project));
  });

  it('keeps retrying when the target cannot be reread (sync)', () => {
    const { venv, pth, project } = venvWithPth();
    const faults = { rename: 1, reread: 0 };
    const fs = {
      ...nodeFs,
      renameSync: ((...args: Parameters<typeof nodeFs.renameSync>) => {
        if (faults.rename > 0) {
          faults.rename--;
          faults.reread++;
          throw refuse('EPERM');
        }
        return nodeFs.renameSync(...args);
      }) as typeof nodeFs.renameSync,
      readFileSync: ((file: string) => {
        if (faults.reread > 0) {
          faults.reread--;
          throw refuse('EBUSY');
        }
        return nodeFs.readFileSync(file);
      }) as typeof nodeFs.readFileSync,
    };

    expect(asciiSafePthFilesSync(venv, fs)).toEqual([pth]);
    expect(faults).toEqual({ rename: 0, reread: 0 });
    expect(readFileSync(pth, 'utf8')).toBe(asciiSafePthText(project));
  });

  it('still gives up on a rename that keeps failing', async () => {
    const { venv } = venvWithPth();
    const fs = {
      ...nodeFsPromises,
      rename: (async () => {
        throw refuse('EPERM');
      }) as typeof nodeFsPromises.rename,
    };
    await expect(asciiSafePthFiles(venv, fs)).rejects.toMatchObject({ code: 'EPERM' });
  });
});
