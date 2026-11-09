import { nativeImage } from 'electron';
import { basename, dirname, join } from 'node:path';

function macOSTrayIconPath(path: string): string {
  const file = {
    '32x32.png': 'trayTemplate.png',
    'tray-recording.png': 'trayRecordingTemplate.png',
  }[basename(path)];
  if (!file) throw new Error(`Unsupported macOS tray icon: ${basename(path)}`);
  return join(dirname(path), file);
}

export function createTrayIcon(path: string, platform: NodeJS.Platform = process.platform) {
  const source = nativeImage.createFromPath(platform === 'darwin' ? macOSTrayIconPath(path) : path);
  if (platform !== 'darwin') return source;
  const icon = source.resize({ width: 18, height: 18, quality: 'best' });
  icon.addRepresentation({
    scaleFactor: 2,
    buffer: source.resize({ width: 36, height: 36, quality: 'best' }).toPNG(),
  });
  icon.setTemplateImage(true);
  return icon;
}
