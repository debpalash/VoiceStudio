import { afterEach, describe, expect, it, vi } from 'vitest';

const resolution = vi.hoisted(() => ({ value: undefined }));
vi.mock('../../resolveDialogEsm.mjs', () => ({ resolveDialogEsm: () => resolution.value }));

afterEach(() => vi.resetModules());

describe('Vite consumes dialog package resolution', () => {
  it.each([undefined, '/workspace/node_modules/@tauri-apps/plugin-dialog/dist-js/index.js'])(
    'builds the correct alias for %s',
    async (entry) => {
      resolution.value = entry;
      const { default: config } = await import('../../vite.config.js');
      const aliases = config.resolve.alias;
      if (entry) expect(aliases['@tauri-apps/plugin-dialog']).toBe(entry);
      else expect(aliases).not.toHaveProperty('@tauri-apps/plugin-dialog');
      expect(aliases['@']).toMatch(/[/\\]src$/);
    },
  );
});
