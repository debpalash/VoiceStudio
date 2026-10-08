import { createServer } from 'node:http';
import assert from 'node:assert/strict';

/** Minimal external backend for native tests whose API traffic is intercepted in Playwright. */
export async function startHealthBackend() {
  const server = createServer((request, response) => {
    const path = new URL(request.url ?? '/', 'http://127.0.0.1').pathname;
    const fixtures = {
      '/health': { status: 'ok', version: 'native-smoke' },
      '/setup/status': { models_ready: true, missing: [] },
      '/api/settings/analytics': { available: false, prompted: true, opted_in: false },
      '/engines': {},
      '/profiles': [],
      '/models': { models: [] },
      '/dictation/prefs': { enabled: false, mode: 'toggle', model_id: null },
      '/dictation/models': { engine_available: false, models: [] },
      '/batch/jobs': [],
      '/history': [],
      '/api/settings/performance-profile': { profile: 'balanced', engines: {} },
      '/workers/target': {
        target: 'local',
        op: '',
        active: { remote: false, label: 'Local device', reason: '' },
        remote_operations: [],
        targets: [
          {
            id: 'local',
            label: 'Local device',
            endpoint: '',
            connected: true,
            available: true,
            detail: '',
            is_local: true,
            status: 'ready',
            latency_ms: 0,
            active_tasks: 0,
            max_tasks: 1,
          },
        ],
      },
      '/model/status': { status: 'not_loaded', message: '' },
      '/model/loaded': { models: [] },
      '/engines/translation': { active: null, engines: [] },
      '/engines/diarisation': { active: null, engines: [] },
      '/system/notifications': [],
      '/export/history': [],
      '/projects': [],
    };
    if (Object.hasOwn(fixtures, path)) {
      response.writeHead(200, {
        'content-type': 'application/json',
        'x-omnivoice-backend': 'native-smoke',
      });
      response.end(JSON.stringify(fixtures[path]));
      return;
    }
    response.writeHead(404);
    response.end();
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  assert(address && typeof address !== 'string');
  return {
    port: address.port,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}
