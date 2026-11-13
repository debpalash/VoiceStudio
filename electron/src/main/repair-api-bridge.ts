import { randomBytes, timingSafeEqual } from 'node:crypto';
import { chmodSync, mkdtempSync, rmSync, rmdirSync, writeFileSync } from 'node:fs';
import { createServer, request as httpRequest, type IncomingHttpHeaders } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { join } from 'node:path';
import { proxyRequestHeaders } from './dev-backend-proxy';

const CAPABILITY_HEADER = 'x-voicestudio-repair-token';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export interface RepairApiBridge {
  contextFile: string;
  mcpConfigFile?: string;
  openCodeConfigFile?: string;
  close: () => Promise<void>;
}

export interface RepairAppControls {
  status: () => unknown;
  restartBackend: () => Promise<void>;
  setupRuntime: () => Promise<void>;
}

const APP_CONTROL_PATHS = {
  status: '/__voicestudio/status',
  restartBackend: '/__voicestudio/backend/restart',
  setupRuntime: '/__voicestudio/backend/setup-runtime',
} as const;

function authorized(headers: IncomingHttpHeaders, capability: Buffer): boolean {
  const value = headers[CAPABILITY_HEADER];
  if (typeof value !== 'string') return false;
  const presented = Buffer.from(value, 'utf8');
  return presented.length === capability.length && timingSafeEqual(presented, capability);
}

function targetFor(baseUrl: string, incomingPath: string): URL | null {
  if (!incomingPath.startsWith('/') || incomingPath.startsWith('//')) return null;
  let path: URL;
  try {
    path = new URL(incomingPath, 'http://repair.invalid');
  } catch {
    return null;
  }
  let decoded: string;
  try {
    decoded = decodeURIComponent(path.pathname).replace(/\/{2,}/g, '/');
  } catch {
    return null;
  }
  // Authentication stays owned by Electron. A repair run never needs to mint,
  // revoke, or inspect the user's real remote session.
  if (/^\/api\/auth(?:\/|$)/i.test(decoded)) return null;
  const target = new URL(baseUrl);
  target.pathname = `${target.pathname.replace(/\/+$/, '')}${path.pathname}`;
  target.search = path.search;
  target.hash = '';
  return target;
}

function responseHeaders(headers: IncomingHttpHeaders): IncomingHttpHeaders {
  const clean = { ...headers };
  delete clean['set-cookie'];
  delete clean.connection;
  delete clean['transfer-encoding'];
  return clean;
}

/**
 * Session-scoped bridge for a user-authorized repair agent. The agent receives
 * only an ephemeral loopback capability; the active backend URL and any remote
 * admin bearer remain in Electron main.
 */
export async function startRepairApiBridge(
  getBaseUrl: () => string,
  getTrustedHeaders: () => Record<string, string>,
  tempRoot: string,
  mode: 'diagnose' | 'fix',
  appControls?: RepairAppControls,
  mcpServerPath?: string,
): Promise<RepairApiBridge> {
  const capabilityText = randomBytes(32).toString('base64url');
  const capability = Buffer.from(capabilityText, 'utf8');
  const directory = mkdtempSync(join(tempRoot, 'voicestudio-repair-api-'), { encoding: 'utf8' });
  const contextFile = join(directory, 'context.json');
  const mcpConfigFile = mcpServerPath ? join(directory, 'mcp.json') : undefined;
  const openCodeConfigFile = mcpServerPath ? join(directory, 'opencode.json') : undefined;
  const server = createServer((incoming, response) => {
    const json = (status: number, body: unknown, headers?: Record<string, string>) => {
      response.writeHead(status, { 'content-type': 'application/json', ...headers });
      response.end(JSON.stringify(body));
    };
    if (!authorized(incoming.headers, capability)) {
      json(401, { detail: 'Repair capability required' });
      return;
    }
    const method = (incoming.method || 'GET').toUpperCase();
    if (mode === 'diagnose' && !SAFE_METHODS.has(method)) {
      json(405, { detail: 'Diagnose mode is read-only' }, { allow: 'GET, HEAD, OPTIONS' });
      return;
    }
    let pathname = '';
    try {
      pathname = new URL(incoming.url || '/', 'http://repair.invalid').pathname;
    } catch {
      json(400, { detail: 'Invalid repair path' });
      return;
    }
    const control = async () => {
      try {
        if (pathname === APP_CONTROL_PATHS.status) {
          if (method !== 'GET' && method !== 'HEAD') {
            json(405, { detail: 'Status is read-only' }, { allow: 'GET, HEAD' });
            return true;
          }
          json(200, appControls?.status() ?? { available: false });
          return true;
        }
        if (pathname === APP_CONTROL_PATHS.restartBackend) {
          if (method !== 'POST') {
            json(405, { detail: 'Restart requires POST' }, { allow: 'POST' });
            return true;
          }
          if (!appControls) {
            json(503, { detail: 'Electron backend controls unavailable' });
            return true;
          }
          await appControls.restartBackend();
          json(202, { accepted: true, operation: 'restart-backend' });
          return true;
        }
        if (pathname === APP_CONTROL_PATHS.setupRuntime) {
          if (method !== 'POST') {
            json(405, { detail: 'Runtime setup requires POST' }, { allow: 'POST' });
            return true;
          }
          if (!appControls) {
            json(503, { detail: 'Electron backend controls unavailable' });
            return true;
          }
          void appControls.setupRuntime().catch(() => {
            // The supervisor publishes the actionable setup failure through status.
          });
          json(202, { accepted: true, operation: 'setup-runtime' });
          return true;
        }
        return false;
      } catch {
        json(500, { detail: 'VoiceStudio could not complete that recovery operation' });
        return true;
      }
    };
    if (pathname.startsWith('/__voicestudio/')) {
      void control().then((handled) => {
        if (!handled && !response.writableEnded)
          json(404, { detail: 'Unknown Electron recovery operation' });
      }).catch(() => {
        if (!response.writableEnded) response.destroy();
      });
      return;
    }
    let target: URL | null = null;
    try {
      target = targetFor(getBaseUrl(), incoming.url || '/');
    } catch {
      // A missing or malformed backend target is reported as a bridge error.
    }
    if (!target) {
      json(403, { detail: 'That backend path is unavailable to repair agents' });
      return;
    }
    const forwarded = { ...incoming.headers };
    delete forwarded[CAPABILITY_HEADER];
    const send = target.protocol === 'https:' ? httpsRequest : httpRequest;
    const upstream = send(
      target,
      {
        method,
        headers: proxyRequestHeaders(forwarded, getTrustedHeaders()),
      },
      (result) => {
        response.writeHead(result.statusCode || 502, responseHeaders(result.headers));
        result.on('error', () => response.destroy());
        result.pipe(response);
      },
    );
    upstream.on('error', () => {
      if (response.headersSent) response.destroy();
      else {
        response.writeHead(502, { 'content-type': 'application/json' });
        response.end('{"detail":"VoiceStudio backend unavailable"}');
      }
    });
    incoming.on('aborted', () => upstream.destroy());
    incoming.on('error', () => upstream.destroy());
    response.on('error', () => upstream.destroy());
    response.on('close', () => {
      if (!response.writableEnded) upstream.destroy();
    });
    incoming.pipe(upstream);
  });
  server.on('clientError', (_error, socket) => socket.destroy());

  try {
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', () => {
        server.off('error', reject);
        resolve();
      });
    });
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Repair API bridge did not bind');
    writeFileSync(
      contextFile,
      JSON.stringify(
        {
          schemaVersion: 1,
          baseUrl: `http://127.0.0.1:${address.port}`,
          headers: { 'X-VoiceStudio-Repair-Token': capabilityText },
          mode,
          lifetime: 'This capability expires when the repair run stops.',
          electronControls: {
            status: { method: 'GET', path: APP_CONTROL_PATHS.status },
            restartBackend: { method: 'POST', path: APP_CONTROL_PATHS.restartBackend },
            setupRuntime: { method: 'POST', path: APP_CONTROL_PATHS.setupRuntime },
            note: 'These controls work while the Python backend is unavailable. Clean reinstall is intentionally not exposed.',
          },
        },
        null,
        2,
      ),
      { encoding: 'utf8', flag: 'wx', mode: 0o600 },
    );
    try {
      chmodSync(contextFile, 0o600);
    } catch {
      // Windows protects the user temp directory with the user's ACL.
    }
    if (mcpConfigFile && mcpServerPath) {
      writeFileSync(
        mcpConfigFile,
        JSON.stringify(
          {
            mcpServers: {
              voicestudio: {
                type: 'stdio',
                command: process.execPath,
                args: [mcpServerPath],
                env: {
                  ELECTRON_RUN_AS_NODE: '1',
                  VOICESTUDIO_REPAIR_CONTEXT_FILE: contextFile,
                },
              },
            },
          },
          null,
          2,
        ),
        { encoding: 'utf8', flag: 'wx', mode: 0o600 },
      );
      try {
        chmodSync(mcpConfigFile, 0o600);
      } catch {
        // Windows protects the user temp directory with the user's ACL.
      }
    }
    if (openCodeConfigFile && mcpServerPath) {
      writeFileSync(
        openCodeConfigFile,
        JSON.stringify(
          {
            $schema: 'https://opencode.ai/config.json',
            permission: {
              '*': 'deny',
              'voicestudio_*': 'allow',
            },
            mcp: {
              voicestudio: {
                type: 'local',
                command: [process.execPath, mcpServerPath],
                enabled: true,
                environment: {
                  ELECTRON_RUN_AS_NODE: '1',
                  VOICESTUDIO_REPAIR_CONTEXT_FILE: contextFile,
                },
              },
            },
          },
          null,
          2,
        ),
        { encoding: 'utf8', flag: 'wx', mode: 0o600 },
      );
      try {
        chmodSync(openCodeConfigFile, 0o600);
      } catch {
        // Windows protects the user temp directory with the user's ACL.
      }
    }
  } catch (error) {
    server.closeAllConnections?.();
    server.close();
    rmSync(contextFile, { force: true });
    if (mcpConfigFile) rmSync(mcpConfigFile, { force: true });
    if (openCodeConfigFile) rmSync(openCodeConfigFile, { force: true });
    try {
      rmdirSync(directory);
    } catch {
      // Best effort after a failed launch.
    }
    throw error;
  }

  let closing: Promise<void> | null = null;
  return {
    contextFile,
    mcpConfigFile,
    openCodeConfigFile,
    close: () => {
      if (closing) return closing;
      closing = new Promise<void>((resolve) => {
        server.close(() => resolve());
        server.closeAllConnections?.();
      }).finally(() => {
        rmSync(contextFile, { force: true });
        if (mcpConfigFile) rmSync(mcpConfigFile, { force: true });
        if (openCodeConfigFile) rmSync(openCodeConfigFile, { force: true });
        try {
          rmdirSync(directory);
        } catch {
          // The context file is gone; an external scanner may briefly hold the directory.
        }
      });
      return closing;
    },
  };
}
