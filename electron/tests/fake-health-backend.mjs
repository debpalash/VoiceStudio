import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import assert from 'node:assert/strict';

export const DEFAULT_FIXTURES = {
  '/health': { status: 'ok', version: 'native-smoke' },
  '/setup/status': { models_ready: true, missing: [] },
  '/api/settings/analytics': { available: false, prompted: true, opted_in: false },
  // The real backend always reports every family; the UI reads `.asr` / `.tts` unguarded.
  '/engines': {
    tts: { active: '', backends: [] },
    asr: { active: '', backends: [] },
    llm: { active: '', backends: [] },
  },
  '/profiles': [],
  '/models': { models: [] },
  '/dictation/prefs': { enabled: false, mode: 'toggle', model_id: null },
  '/dictation/models': { engine_available: false, models: [] },
  '/batch/jobs': [],
  '/history': [],
  // The Calls view at #/calls (Workflows moved to #/workflows): no Twilio set up, no calls yet.
  '/calls': { calls: [] },
  // backend/services/telephony/calls.py readiness() returns a bare list.
  '/calls/readiness': [],
  '/calls/settings': {
    from_number: '',
    disclosure_template: '',
    inbound_mode: 'greeting',
    inbound_brief: '',
    max_concurrent: 1,
    record_calls: false,
  },
  '/api/settings/performance-profile': {
    global: 'balanced',
    overrides: {},
    effective: {
      tts: 'balanced',
      asr: 'balanced',
      dictation: 'balanced',
      diarisation: 'balanced',
      translation: 'balanced',
      llm: 'balanced',
    },
    families: ['tts', 'asr', 'dictation', 'diarisation', 'translation', 'llm'],
    implemented_families: ['tts', 'asr', 'dictation', 'diarisation', 'translation'],
    applicable_families: ['tts', 'asr', 'dictation', 'translation'],
    targets: {
      tts: { steps: 16, postprocess: true },
      asr: { engine: 'faster-whisper', beam_size: 3, best_of: 3 },
      dictation: { engine: 'sherpa-onnx', max_active_paths: 4 },
      diarisation: { engine: 'audiocpp-sortformer' },
      translation: { engine: 'nllb', num_beams: 3 },
    },
    selections: {
      tts: { engine: 'omnivoice', model: 'k2-fsa/OmniVoice' },
      asr: { engine: 'faster-whisper', model: 'deepdml/faster-whisper-large-v3-turbo-ct2' },
      dictation: {
        engine: 'offline-transducer',
        model: 'sherpa-parakeet-tdt-v3',
        label: 'Fixture',
      },
      diarisation: { engine: 'inactive', model: null },
      translation: { engine: 'nllb', model: 'facebook/nllb-200-distilled-600M' },
      llm: { engine: 'inactive', model: null },
    },
  },
  '/api/settings/compute-device': {
    value: 'auto',
    applied: 'auto',
    effective_family: 'cpu',
    available_families: ['cpu'],
    env_pinned: false,
    override_ignored: false,
    restart_required: false,
  },
  '/api/settings/perf/torch-compile-disabled': { platform: process.platform, enabled: false },
  '/model/status': { status: 'not_loaded', message: '' },
  '/model/loaded': { models: [] },
  '/engines/translation': { active: null, engines: [] },
  '/engines/diarisation': { active: 'off', options: [] },
  '/engines/omnivoice-gguf/gpu-offer': {
    eligible: false,
    answer: null,
    active: false,
    show: false,
    licence_accepted: false,
    gpu_name: null,
    device: null,
    vendor: null,
    shared_memory: false,
    effective_vram_gb: null,
    quant: null,
    download_mb: 0,
  },
  '/engines/audiocpp/runtime/install/status': {
    installed: false,
    supported: true,
    job: { state: 'idle', progress: 0 },
  },
  '/system/notifications': [],
  '/export/history': [],
  '/projects': [],
  '/models/install/status': { jobs: [] },
  '/generate/budget': {},
  '/setup/preflight': {
    ok: true,
    checks: [{ id: 'ram', label: 'Memory', status: 'pass', detail: 'Enough memory', fix: null }],
  },
  // Matches `/setup/status` above: every model is already installed.
  '/setup/recommendations': {
    device: { label: 'Fixture CPU' },
    models: [
      {
        repo_id: 'k2-fsa/OmniVoice',
        label: 'VoiceStudio TTS',
        role: 'TTS',
        size_gb: 2.4,
        required: true,
        installed: true,
      },
    ],
    download_gb_remaining: 0,
    total_gb: 2.4,
    all_installed: true,
  },
  // Settings, gallery and long-form routes. Shapes follow the renderer's own types; keep each
  // as empty/idle as the real backend is on a fresh install.
  '/api/mcp/bindings': [],
  '/personalities': [],
  '/pronunciation': [],
  '/archetypes': { items: [], total: 0, offset: 0, limit: 60 },
  '/archetypes/categories': [],
  '/archetypes/previews/status': {},
  '/audiobook/jobs': { jobs: [] },
  '/longform/jobs': { jobs: [] },
  '/system/logs': { lines: [], exists: true, path: 'fixture.log' },
  '/api/settings/db-backup': { available: true, latest: null },
  '/api/settings/llm-providers': { active: '', engine_active: 'off', providers: [] },
  '/api/settings/llm-skills': { skills: [] },
  '/api/settings/hf-mirror': {
    configured: '',
    effective: '',
    mode: 'auto',
    presets: [{ label: 'Official', url: 'https://huggingface.co' }],
    auto: null,
  },
  '/api/settings/asr-openai-compat': {},
  '/api/settings/changelog': {},
  '/api/settings/cuda-device': {
    value: 'auto',
    applied: 'auto',
    env_pinned: false,
    restart_required: false,
    devices: [],
  },
  '/api/settings/dictation-refinement': {},
  '/api/settings/gpu-report': {
    platform: process.platform,
    gpus: [],
    torch: { kind: 'cpu', version: null },
    state: 'cpu_only',
    params: {},
    options: [],
    engines: [],
  },
  '/api/settings/hf-token/state': { active: null, sources: [] },
  '/api/settings/perf/offload-after-generation': {
    enabled: false,
    env_pinned: false,
    device: 'cpu',
  },
  '/api/settings/pause-fill': {
    mode: 'silence',
    env_pinned: false,
    choices: ['silence', 'room_tone'],
  },
  '/api/settings/storage': { categories: [], volumes: [], warnings: [] },
  '/api/settings/storage/models-dir': {},
  '/demo_audio/demo/dubbing/manifest.json': {
    source: { code: 'en', label: 'English', video: 'source.mp4', script: 'source.txt' },
    dubbed: [],
  },
  '/openapi.json': {},
  '/stats/usage': {},
  '/sysinfo': {
    cpu: 0,
    cpu_model: 'Fixture CPU',
    cpu_physical_cores: 1,
    cpu_logical_cores: 1,
    cpu_frequency_ghz: 1,
    ram: 1,
    total_ram: 8,
    gpu_name: '',
    gpu_utilization: null,
    vram: 0,
    total_vram: 0,
    gpu_active: false,
  },
  '/system/network/state': {},
  '/system/tailscale/status': {},
  '/dictation/readiness': { ready: true },
  // HEAD probe for the bundled dictation demo clip shown on the last setup step.
  '/demo_audio/dictation/en_conversational.wav': {},
  '/system/info': {
    translate_provider: 'unknown',
    platform: process.platform,
    arch: process.arch,
    python: '3.11',
    generate_timeout_s: 600,
    cpu_generate_timeout_s: 1800,
    generate_timeout_shadowed: false,
    cpu_generate_timeout_shadowed: false,
  },
  '/watermark/status': { audioseal_available: true, invisible_enabled: true },
  '/api/settings/history-retention': { cap: 200, default: 200 },
  '/media-tools/status': {
    tools: {
      ffmpeg: { ok: true, version: 'fixture', origin: 'system' },
      ffprobe: { ok: true, origin: 'system' },
      ytdlp: { version: 'fixture', overlay_version: null },
    },
    ops: {},
  },
};

const WEBSOCKET_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

const readBody = (request) =>
  new Promise((resolve, reject) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.once('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    request.once('error', reject);
  });

/**
 * Minimal external backend for native tests whose API traffic is intercepted in Playwright.
 *
 * `fixtures` is merged over the defaults; a value may be a function `(request, body) => json`.
 * Every request is appended to `requests` before its response is written.
 */
export async function startHealthBackend({ fixtures = {}, port = 0 } = {}) {
  const served = { ...DEFAULT_FIXTURES, ...fixtures };
  const requests = [];
  const sockets = new Set(); // upgraded sockets are invisible to closeAllConnections()
  const server = createServer(async (request, response) => {
    const path = new URL(request.url ?? '/', 'http://127.0.0.1').pathname;
    const body = await readBody(request);
    const known = Object.hasOwn(served, path);
    requests.push({ method: request.method ?? 'GET', path, status: known ? 200 : 404, body });
    if (known) {
      const fixture = served[path];
      response.writeHead(200, {
        'content-type': 'application/json',
        'x-omnivoice-backend': 'native-smoke',
      });
      response.end(
        JSON.stringify(typeof fixture === 'function' ? fixture(request, body) : fixture),
      );
      return;
    }
    response.writeHead(404);
    response.end();
  });
  // The app opens /ws/events for live status; accept the handshake and never send a frame.
  server.on('upgrade', (request, socket) => {
    const key = request.headers['sec-websocket-key'];
    if (typeof key !== 'string') {
      socket.destroy();
      return;
    }
    const accept = createHash('sha1')
      .update(key + WEBSOCKET_GUID)
      .digest('base64');
    socket.write(
      'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n' +
        `Sec-WebSocket-Accept: ${accept}\r\n\r\n`,
    );
    sockets.add(socket);
    socket.on('error', () => {});
    socket.once('close', () => sockets.delete(socket));
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', resolve);
  });
  const address = server.address();
  assert(address && typeof address !== 'string');
  return {
    port: address.port,
    requests,
    close: () => {
      server.closeAllConnections();
      for (const socket of sockets) socket.destroy();
      return new Promise((resolve) => server.close(resolve));
    },
  };
}
