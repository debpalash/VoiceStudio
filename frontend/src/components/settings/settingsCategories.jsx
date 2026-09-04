/**
 * Settings category registry — the single source of truth for the sidebar IA.
 *
 * The Settings page is a sidebar-nav + content-pane hub (macOS System Settings
 * / VS Code style). This module declares the four sidebar GROUPS and their
 * categories (id, group, label, lucide icon, restart flag, and a small set of
 * searchable setting keywords). Settings.jsx renders the sidebar from GROUPS
 * and switches the content pane on the active category id.
 *
 * Keep this declarative — no JSX panels here (those need props/hooks and live
 * in Settings.jsx's renderCategory switch). `keywords` powers the bonus
 * "search matches a setting → jump to its category" behaviour; `keywordKeys`
 * lists i18n keys of prominent setting-row titles so the same search works in
 * every UI language (the translated titles are matched at query time — no
 * separate keyword translations to maintain).
 */
import {
  AudioLines,
  Palette,
  Plug,
  Cpu,
  Mic,
  SpellCheck,
  Languages,
  Brain,
  Gauge,
  HardDrive,
  Wifi,
  Share2,
  KeyRound,
  LockKeyhole,
  Sparkles,
  ArrowDownToLine,
  ShieldCheck,
  FileText,
  Info,
  Braces,
  BarChart3,
} from 'lucide-react';

/** Sidebar groups, in display order. `labelKey` resolves via i18n. */
export const GROUPS = [
  {
    id: 'general',
    labelKey: 'settings.group_general',
    defaultLabel: 'General',
    items: [
      {
        id: 'appearance',
        labelKey: 'settings.appearance',
        defaultLabel: 'Appearance',
        icon: Palette,
        keywords: [
          'theme',
          'color theme',
          'ui scale',
          'font',
          'auto-play preview',
          'header live stats',
          'system metrics',
          'navigation style',
          'sidebar rail',
          'titlebar tabs',
          'tabs',
          'language',
          'locale',
          'interface language',
          'review mode',
          'stage checkpoints',
        ],
        keywordKeys: [
          'settings.ui_scale',
          'settings.color_theme',
          'settings.font',
          'settings.autoplay_preview',
          'settings.header_live_stats',
          'settings.nav_style',
          // The visible option labels too, so a non-English search for "tabs"
          // / "Leiste" matches what the user actually sees on screen.
          'settings.nav_style_rail',
          'settings.nav_style_tabs',
          'settings.language',
          'settings.review_mode',
        ],
      },
    ],
  },
  {
    id: 'voice',
    labelKey: 'settings.group_voice',
    defaultLabel: 'Voice & Engines',
    items: [
      {
        id: 'engines',
        labelKey: 'settings.engines',
        defaultLabel: 'Engines',
        icon: Plug,
        // Engine selection lives in the Model Catalogue workspace now; the
        // category stays so the old name still finds it (it renders a pointer).
        keywords: [
          'engine',
          'catalogue',
          'catalog',
          'model catalogue',
          'default engine',
          'tts engine',
          'indextts',
          'cosyvoice',
          'compatibility',
          'gpu',
          'asr',
          'transcription',
          'whisper',
          'openai-compatible',
          'remote asr',
        ],
      },
      {
        id: 'models',
        labelKey: 'settings.models',
        defaultLabel: 'Models',
        icon: Cpu,
        restart: true,
        keywords: [
          'model',
          'download',
          'cache directory',
          'models directory',
          'hugging face mirror',
          'hf_endpoint',
          'catalogue',
          'catalog',
          'model store',
        ],
      },
      {
        id: 'dictation',
        labelKey: 'settings.dictation',
        defaultLabel: 'Dictation',
        icon: Mic,
        keywords: [
          'dictation',
          'hotkey',
          'shortcut',
          'refinement',
          'echo cancellation',
          'aec',
          'microphone',
          'voice capture',
        ],
        keywordKeys: ['settings.shortcut'],
      },
      {
        id: 'pronunciation',
        labelKey: 'settings.pronunciation',
        defaultLabel: 'Pronunciation',
        icon: SpellCheck,
        keywords: ['pronunciation', 'lexicon', 'phoneme', 'g2p', 'dictionary'],
      },
      {
        id: 'translation',
        labelKey: 'settings.translation',
        defaultLabel: 'Translation',
        icon: Languages,
        keywords: [
          'translation',
          'translate quality',
          'cinematic',
          'llm endpoint',
          'deepl',
          'microsoft',
          'openai',
          'api key',
        ],
        keywordKeys: ['settings.translate_quality', 'settings.translation_providers'],
      },
    ],
  },
  {
    id: 'system',
    labelKey: 'settings.group_system',
    defaultLabel: 'System',
    items: [
      {
        id: 'performance',
        labelKey: 'settings.performance',
        defaultLabel: 'Performance & Device',
        icon: Gauge,
        restart: true,
        keywords: [
          'performance',
          'torch.compile',
          'device',
          'gpu',
          'ram',
          'vram',
          'compute',
          'platform',
          'cuda',
          'rocm',
          'mps',
          'cpu',
          'xpu',
          'intel',
          'timeout',
          'generation timeout',
          'compute budget',
          'compute-time budget',
        ],
        keywordKeys: [
          'settings.generate_budget_title',
          'settings.generate_timeout_gpu',
          'settings.generate_timeout_cpu',
        ],
      },
      {
        id: 'usage',
        labelKey: 'settings.usage',
        defaultLabel: 'Usage',
        icon: BarChart3,
        keywords: [
          'usage',
          'stats',
          'statistics',
          'insights',
          'analytics',
          'history',
          'how much',
          'privacy',
        ],
      },
      {
        id: 'storage',
        labelKey: 'settings.storage',
        defaultLabel: 'Storage',
        icon: HardDrive,
        keywords: [
          'storage',
          'data directory',
          'outputs directory',
          // "factory reset" is what users search for even though the feature is
          // now the broader "Reset & remove" — keep the old name findable.
          'factory reset',
          'reset',
          'wipe',
          'delete models',
          'uninstall',
          'remove all data',
          'start over',
          'disk usage',
          'free space',
          'disk space',
          'model cache size',
          'engine venvs',
          'temp files',
          'clear logs',
        ],
        keywordKeys: ['settings.storage_usage', 'settings.reset', 'settings.uninstall'],
      },
      {
        id: 'permissions',
        // Lives in the permissions.* i18n namespace (not settings.*) so the
        // whole feature's strings ship as one additive block per locale.
        labelKey: 'permissions.title',
        defaultLabel: 'Permissions',
        icon: LockKeyhole,
        keywords: [
          'permission',
          'permissions',
          'microphone access',
          'mic access',
          'accessibility',
          'privacy & security',
          'os permissions',
          'grant',
          'tcc',
        ],
        keywordKeys: ['permissions.microphone', 'permissions.accessibility'],
      },
      {
        id: 'network',
        labelKey: 'settings.network',
        defaultLabel: 'Network',
        icon: Wifi,
        // Only the proxy lives here now (applies immediately) — the
        // restart-bound FFmpeg override moved to Audio tools below.
        keywords: ['network', 'proxy', 'http proxy', 'socks'],
        keywordKeys: ['settings.proxy'],
      },
      {
        id: 'audio-tools',
        labelKey: 'settings.audio_tools',
        defaultLabel: 'Audio tools',
        icon: AudioLines,
        // yt-dlp updates land in an overlay read at process start (the row
        // renders RestartBadge) — lockstep-guarded in
        // settingsCategories.test.jsx like Models / Performance / Sharing.
        restart: true,
        keywords: [
          'ffmpeg',
          'ffprobe',
          'ffmpeg path',
          'yt-dlp',
          'ytdlp',
          'media engine',
          'video downloader',
          'bundled binaries',
        ],
        keywordKeys: ['settings.audio_tools', 'settings.ffmpeg', 'settings.audio_tools_ytdlp'],
      },
      {
        id: 'workers',
        labelKey: 'settings.workers_title',
        defaultLabel: 'Remote workers',
        icon: Cpu,
        // Its own System entry rather than a section inside Sharing: this
        // sends work OUT to machines you own, where everything under Sharing
        // is about letting something else reach this one.
        keywords: [
          'remote workers',
          'workers',
          'gpu',
          'second gpu',
          'another machine',
          'distributed',
          'enrollment token',
          'offload',
          'join code',
          'join',
          'qr',
          'lend gpu',
          'share gpu',
          'worker mode',
        ],
        keywordKeys: ['settings.workers_title', 'settings.workers_add'],
      },
      {
        id: 'sharing',
        labelKey: 'settings.sharing',
        defaultLabel: 'Sharing & Remote',
        icon: Share2,
        restart: true,
        keywords: ['sharing', 'remote backend', 'mcp', 'tailscale', 'gpu box', 'bindings'],
      },
      {
        id: 'openapi',
        labelKey: 'settings.openapi',
        defaultLabel: 'VoiceStudio API',
        icon: Braces,
        keywords: ['api', 'openapi', 'scalar', 'rest', 'swagger', 'docs', 'reference', 'endpoints'],
      },
      {
        id: 'credentials',
        labelKey: 'settings.credentials',
        defaultLabel: 'Credentials',
        icon: KeyRound,
        keywords: ['credentials', 'hugging face token', 'hf token', 'api key', 'secret'],
        keywordKeys: ['settings.hf_token_title'],
      },
      {
        id: 'llm-providers',
        labelKey: 'settings.llm_providers',
        defaultLabel: 'LLM Providers',
        icon: Brain,
        keywords: [
          'llm',
          'provider',
          'api key',
          'openai',
          'openrouter',
          'orcarouter',
          'groq',
          'ollama',
          'gemini',
          'cinematic',
          'autofit',
          'translation quality',
        ],
        keywordKeys: ['settings.llmp_provider', 'settings.llmp_api_key', 'settings.llmp_model'],
      },
      {
        id: 'llm-skills',
        labelKey: 'settings.llm_skills',
        defaultLabel: 'LLM Skills',
        icon: Sparkles,
        keywords: [
          'llm',
          'skills',
          'ai features',
          'routing',
          'local model',
          'ollama',
          'lm studio',
          'cinematic',
          'refinement',
          'glossary',
          'direction',
          'slot fitting',
        ],
      },
    ],
  },
  {
    id: 'app',
    labelKey: 'settings.group_app',
    defaultLabel: 'App',
    items: [
      {
        id: 'updates',
        labelKey: 'settings.updates',
        defaultLabel: 'Updates',
        icon: ArrowDownToLine,
        keywords: ['update', 'channel', 'stable', 'preview', 'releases', 'changelog'],
      },
      {
        id: 'privacy',
        labelKey: 'settings.privacy',
        defaultLabel: 'Privacy & Reporting',
        icon: ShieldCheck,
        keywords: ['privacy', 'reporting', 'telemetry', 'tracking', 'network calls'],
      },
      {
        id: 'logs',
        labelKey: 'settings.logs',
        defaultLabel: 'Logs',
        icon: FileText,
        keywords: ['logs', 'backend log', 'frontend log', 'tauri log', 'report a bug'],
      },
      {
        id: 'about',
        labelKey: 'settings.about',
        defaultLabel: 'About',
        icon: Info,
        keywords: ['about', 'version', 'license', 'diagnostics', 'self check'],
        keywordKeys: ['about.version', 'about.self_check'],
      },
    ],
  },
];

/** Flat list of every category, in sidebar order. */
export const CATEGORIES = GROUPS.flatMap((g) => g.items.map((it) => ({ ...it, group: g.id })));

/** Fast lookup: category id → category record. */
export const CATEGORY_BY_ID = Object.fromEntries(CATEGORIES.map((c) => [c.id, c]));

/** The category shown on first open (and the deep-link/persist fallback). */
export const DEFAULT_CATEGORY = 'appearance';

/**
 * Map legacy Settings tab ids (the old 11-tab shell, still used by deep-links
 * like the footer version badge → 'updates') onto the new category ids. Any id
 * not listed is assumed to already be a valid new category id.
 */
export const LEGACY_TAB_MAP = {
  general: 'appearance',
  capture: 'dictation',
};

/** Resolve any incoming tab/category id to a valid new category id. */
export function resolveCategoryId(id) {
  if (!id) return DEFAULT_CATEGORY;
  const mapped = LEGACY_TAB_MAP[id] || id;
  return CATEGORY_BY_ID[mapped] ? mapped : DEFAULT_CATEGORY;
}

/**
 * Given a lowercased query, return the set of category ids whose label OR any
 * keyword matches. Used to filter the sidebar and to power "search a setting →
 * jump to its category".
 *
 * @param {string}    query
 * @param {function=} labelFor   (category) => translated label
 * @param {function=} translate  i18n `t` — lets `keywordKeys` (setting-row
 *   title keys) match in the active UI language, so a German user finds
 *   Appearance by "Schriftart" just like an English user finds it by "font".
 *   The English `keywords` always match too, in every locale.
 */
export function matchCategories(query, labelFor, translate) {
  const q = query.trim().toLowerCase();
  if (!q) return CATEGORIES.map((c) => c.id);
  return CATEGORIES.filter((c) => {
    const label = (labelFor ? labelFor(c) : c.defaultLabel).toLowerCase();
    if (label.includes(q)) return true;
    if ((c.keywords || []).some((k) => k.toLowerCase().includes(q))) return true;
    if (!translate) return false;
    return (c.keywordKeys || []).some((key) => String(translate(key)).toLowerCase().includes(q));
  }).map((c) => c.id);
}
