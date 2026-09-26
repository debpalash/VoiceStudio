import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import {
  createMacApplicationMenuTemplate,
  installAppIdentity,
  installMacApplicationMenu,
} from './app-identity';

describe('installAppIdentity', () => {
  it('names Electron internals consistently', () => {
    const setName = vi.fn();

    installAppIdentity({ setName });

    expect(setName).toHaveBeenCalledOnce();
    expect(setName).toHaveBeenCalledWith('VoiceStudio');
  });

  it('defines the complete native macOS menu with VoiceStudio app labels', () => {
    const template = createMacApplicationMenuTemplate();

    expect(template[0]).toMatchObject({ label: 'VoiceStudio' });
    expect(template[0]?.submenu).toEqual(
      expect.arrayContaining([{ role: 'about' }, { role: 'hide' }, { role: 'quit' }]),
    );
    expect(template.slice(1)).toEqual([
      { role: 'fileMenu' },
      { role: 'editMenu' },
      { role: 'viewMenu' },
      { role: 'windowMenu' },
    ]);
  });

  it('installs the identity before Electron begins ready initialization', () => {
    const entrypoint = readFileSync(resolve(process.cwd(), 'src/main/index.ts'), 'utf8');
    const identityInstall = entrypoint.indexOf('installAppIdentity(app);');
    const readyInitialization = entrypoint.indexOf('.whenReady()');

    expect(identityInstall).toBeGreaterThanOrEqual(0);
    expect(readyInitialization).toBeGreaterThan(identityInstall);
  });

  it('brands the About panel and installs the macOS menu', () => {
    const application = {
      setAboutPanelOptions: vi.fn(),
    };
    const nativeMenu = { id: 'native-menu' };
    const menu = {
      buildFromTemplate: vi.fn(() => nativeMenu),
      setApplicationMenu: vi.fn(),
    };

    installMacApplicationMenu(application, menu, '0.5.6');

    expect(application.setAboutPanelOptions).toHaveBeenCalledWith({
      applicationName: 'VoiceStudio',
      applicationVersion: '0.5.6',
      version: '0.5.6',
    });
    expect(menu.setApplicationMenu).toHaveBeenCalledWith(nativeMenu);
  });
});
