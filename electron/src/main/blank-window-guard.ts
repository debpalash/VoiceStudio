import type { BrowserWindow } from 'electron';
import { isTrustedRenderer } from './trusted-renderer';

const FIRST_CHECK_MS = 12_000;
const RETRY_BASE_MS = 6_000;
const HEARTBEAT_MS = 30_000;
const PROBE_TIMEOUT_MS = 4_000;
const MAX_RELOADS = 3;
export const BLANK_CACHE_REPAIR_URL = 'app://voicestudio/__renderer-cache-repair';

type FallbackCopy = { title: string; retry: string };

// Generated from common.error and errors.tryAgain in the renderer catalogues.
const FALLBACK_COPY: Record<string, FallbackCopy> = {
  ar: { title: 'حدث خطأ ما', retry: 'حاول مرة أخرى' },
  de: { title: 'Etwas ist schief gelaufen', retry: 'Versuchen Sie es erneut' },
  en: { title: 'Something went wrong', retry: 'Try again' },
  es: { title: 'algo salió mal', retry: 'Inténtalo de nuevo' },
  fr: { title: "Quelque chose s'est mal passé", retry: 'Réessayez' },
  hi: { title: 'कुछ ग़लत हो गया', retry: 'पुनः प्रयास करें' },
  id: { title: 'Ada yang tidak beres', retry: 'Coba lagi' },
  it: { title: 'Qualcosa è andato storto', retry: 'Riprova' },
  ja: { title: '何か問題が発生しました', retry: 'もう一度試してください' },
  ko: { title: '문제가 발생했습니다.', retry: '다시 시도하세요' },
  nl: { title: 'Er is iets misgegaan', retry: 'Probeer het opnieuw' },
  pl: { title: 'Coś poszło nie tak', retry: 'Spróbuj ponownie' },
  pt: { title: 'Algo deu errado', retry: 'Tente novamente' },
  ru: { title: 'Что-то пошло не так', retry: 'Попробуйте еще раз' },
  sv: { title: 'Något gick fel', retry: 'Försök igen' },
  th: { title: 'มีบางอย่างผิดพลาด', retry: 'ลองอีกครั้ง' },
  tr: { title: 'Bir şeyler ters gitti', retry: 'Tekrar dene' },
  uk: { title: 'Щось пішло не так', retry: 'Спробуйте знову' },
  vi: { title: 'Đã xảy ra lỗi', retry: 'Thử lại' },
  'zh-CN': { title: '出了点问题', retry: '再试一次' },
  'zh-TW': { title: '出了點問題', retry: '再試一次' },
};

export function fallbackLocale(locale: string): string {
  const normalized = locale.replace('_', '-');
  const lower = normalized.toLowerCase();
  if (lower.startsWith('zh')) return /(?:hant|tw|hk|mo)/i.test(normalized) ? 'zh-TW' : 'zh-CN';
  const exact = Object.keys(FALLBACK_COPY).find((key) => key.toLowerCase() === lower);
  if (exact) return exact;
  const language = lower.split('-')[0];
  return Object.hasOwn(FALLBACK_COPY, language) ? language : 'en';
}

export function localizedFallbackCopy(locale: string): FallbackCopy {
  return FALLBACK_COPY[fallbackLocale(locale)] || FALLBACK_COPY.en;
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function blankFallbackUrl(locale: string): string {
  const copy = localizedFallbackCopy(locale);
  const html = `<!doctype html><html lang="${escapeHtml(fallbackLocale(locale))}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>VoiceStudio</title><style>html{color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#150f19;color:#f7eef7;font:15px/1.5 Inter,system-ui,sans-serif}.card{width:min(28rem,calc(100vw - 3rem));padding:2.5rem;text-align:center;border:1px solid #ffffff18;border-radius:1.5rem;background:#ffffff08;box-shadow:0 24px 80px #0008}.mark{display:grid;place-items:center;width:3.5rem;height:3.5rem;margin:0 auto 1.25rem;border-radius:1rem;background:#d20a681c;color:#ff5aa5;font-size:1.75rem}h1{margin:0 0 1.5rem;font-size:1.25rem}button{border:1px solid #ffffff1f;border-radius:.75rem;background:#c70a60;color:white;padding:.7rem 1.15rem;font:inherit;font-weight:650;cursor:pointer}button:hover{background:#df1672}</style></head><body><main class="card"><div class="mark" aria-hidden="true">⌁</div><h1>${escapeHtml(copy.title)}</h1><button type="button" onclick="location.replace('${BLANK_CACHE_REPAIR_URL}')">${escapeHtml(copy.retry)}</button></main></body></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

async function hasRenderedRoot(win: BrowserWindow): Promise<boolean> {
  if (win.isDestroyed() || win.webContents.isDestroyed()) return true;
  let timeout: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      win.webContents.executeJavaScript(
        "Boolean(document.getElementById('root')?.childElementCount)",
        true,
      ),
      new Promise<false>((resolve) => {
        timeout = setTimeout(() => resolve(false), PROBE_TIMEOUT_MS);
      }),
    ]);
  } catch {
    return false;
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

export function installBlankWindowGuard(
  win: BrowserWindow,
  appUrl: string,
  locale: string,
  devOrigin?: string,
  repairCacheAndRelaunch?: () => Promise<void>,
): () => void {
  // Keep the EventEmitter reference while the window is alive. Electron's
  // `win.webContents` getter itself throws after the native window is gone.
  const contents = win.webContents;
  let timer: NodeJS.Timeout | undefined;
  let reloads = 0;
  let stopped = false;
  let showingFallback = false;
  let repairRunning = false;
  let revision = 0;

  const schedule = (delay: number) => {
    if (stopped || win.isDestroyed()) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void inspect(), delay);
  };
  const inspect = async () => {
    if (stopped || showingFallback || win.isDestroyed()) return;
    const generation = revision;
    const rendered = await hasRenderedRoot(win);
    // A probe belongs to the document it inspected, never to a replacement
    // navigation or a guard/window that was retired while Chromium answered.
    if (stopped || win.isDestroyed() || showingFallback || generation !== revision) return;
    if (rendered) {
      reloads = 0;
      schedule(HEARTBEAT_MS);
      return;
    }
    reloads += 1;
    if (reloads <= MAX_RELOADS) {
      console.warn(`[window] empty renderer; reload ${reloads}/${MAX_RELOADS}`);
      void win.loadURL(appUrl).catch(() => {});
      schedule(RETRY_BASE_MS * reloads);
      return;
    }
    showingFallback = true;
    console.error('[window] renderer remained empty; showing built-in recovery');
    void win.loadURL(blankFallbackUrl(locale)).catch(() => {});
  };
  const loaded = (_event: Electron.Event, url: string) => {
    if (!isTrustedRenderer(url, devOrigin)) return;
    revision++;
    const manualRecovery = showingFallback;
    showingFallback = false;
    if (manualRecovery) reloads = 0;
    schedule(reloads ? RETRY_BASE_MS * reloads : FIRST_CHECK_MS);
  };
  const navigationStarted = (_event: Electron.Event, _url: string, _inPlace: boolean, isMainFrame: boolean) => {
    if (!isMainFrame) return;
    revision++;
    schedule(reloads ? RETRY_BASE_MS * reloads : FIRST_CHECK_MS);
  };
  const closed = () => stop();
  const repairRequested = (event: Electron.Event, url: string) => {
    if (url !== BLANK_CACHE_REPAIR_URL) return;
    event.preventDefault();
    if (!showingFallback || repairRunning || !repairCacheAndRelaunch) return;
    repairRunning = true;
    void repairCacheAndRelaunch().catch((error) => {
      repairRunning = false;
      console.error('[window] renderer cache repair failed', error);
    });
  };
  const stop = () => {
    if (stopped) return;
    stopped = true;
    revision++;
    if (timer) clearTimeout(timer);
    if (!contents.isDestroyed()) {
      contents.off('did-navigate', loaded);
      contents.off('did-start-navigation', navigationStarted);
      contents.off('will-navigate', repairRequested);
    }
    win.off('closed', closed);
  };

  contents.on('did-navigate', loaded);
  contents.on('did-start-navigation', navigationStarted);
  contents.on('will-navigate', repairRequested);
  win.on('closed', closed);
  schedule(FIRST_CHECK_MS);
  return stop;
}
