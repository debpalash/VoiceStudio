import type { Session } from 'electron';
import { isTrustedRenderer } from './trusted-renderer';

const TRUSTED_RENDERER_PERMISSIONS = new Set([
  'clipboard-read',
  'clipboard-sanitized-write',
  'fullscreen',
]);

export function allowsRendererPermission(
  permission: string,
  requestingUrl: string,
  mediaTypes: readonly string[] | undefined,
  devOrigin?: string,
  isMainFrame = true,
): boolean {
  if (!isMainFrame || !isTrustedRenderer(requestingUrl, devOrigin)) return false;
  if (permission !== 'media') return TRUSTED_RENDERER_PERMISSIONS.has(permission);
  return !mediaTypes?.includes('video');
}

/**
 * Electron requires both handlers for complete Chromium permission handling.
 * VoiceStudio grants its trusted renderer audio capture and the small set of
 * browser permissions its existing controls use; every foreign origin and
 * camera request remains denied.
 */
export function installRendererPermissions(session: Session, devOrigin?: string): void {
  session.setPermissionCheckHandler(
    (webContents, permission, requestingOrigin, details) =>
      allowsRendererPermission(
        permission,
        details.requestingUrl || requestingOrigin || webContents?.getURL() || '',
        details.mediaType ? [details.mediaType] : undefined,
        devOrigin,
        details.isMainFrame,
      ),
  );
  session.setPermissionRequestHandler((webContents, permission, callback, details) => {
    const request = details as Electron.MediaAccessPermissionRequest;
    callback(
      allowsRendererPermission(
        permission,
        details.requestingUrl || request.securityOrigin || webContents.getURL(),
        request.mediaTypes,
        devOrigin,
        details.isMainFrame,
      ),
    );
  });
}
