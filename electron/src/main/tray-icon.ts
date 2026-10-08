import { nativeImage } from 'electron';

export function createTrayIcon(path: string, platform: NodeJS.Platform = process.platform) {
  const source = nativeImage.createFromPath(path);
  if (platform !== 'darwin') return source;
  // Menu-bar images use logical points, not the app icon's source pixel size.
  const icon = source.resize({ width: 18, height: 18, quality: 'best' });
  icon.addRepresentation({
    scaleFactor: 2,
    buffer: source.resize({ width: 36, height: 36, quality: 'best' }).toPNG(),
  });
  return icon;
}
