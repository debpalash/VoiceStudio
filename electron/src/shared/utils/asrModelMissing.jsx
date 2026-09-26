/**
 * asrModelMissing — typed "no speech-to-text model installed" error + CTA.
 *
 * Only the TTS model is required (backend models.yaml): a fresh install has
 * no ASR model on disk. Backends answer ASR requests on such an install with
 * a typed payload instead of 500ing or silently downloading multi-GB weights:
 *
 *   HTTP 409  { detail: { error: 'asr_model_missing', recommended: {…} } }
 *   SSE error { detail, error: 'asr_model_missing', recommended: {…} }
 *   WS frame  { type: 'error', kind: 'asr_model_missing', recommended: {…} }
 *
 * `asrMissingPayload` normalizes all three shapes (plus an Error the SSE
 * handler tagged with `.asrModelMissing`); `toastAsrModelMissing` renders the
 * one-click "Download {label} ({size} GB)" CTA that follows the install to a
 * terminal state instead of treating the background-task acknowledgement as
 * success. Same toast-with-action pattern as errorToast.jsx.
 */
import toast from 'react-hot-toast';
import i18next from 'i18next';
import { installModel, listModels, setupDownloadStreamUrl } from '../api/setup';
import { apiPost } from '../api/client';

export const ASR_MODEL_MISSING = 'asr_model_missing';

function installPercent(event) {
  if (event.phase === 'aggregate') {
    const total = Number(event.total_bytes) || 0;
    const files = Number(event.files_total) || 0;
    const bytePct = total > 0 ? ((Number(event.bytes_done) || 0) / total) * 100 : 0;
    const filePct = files > 0 ? ((Number(event.files_done) || 0) / files) * 100 : 0;
    return Math.min(99, Math.max(bytePct, filePct));
  }
  const raw = Number(event.pct);
  if (!Number.isFinite(raw) || raw < 0) return null;
  return Math.min(99, raw <= 1 ? raw * 100 : raw);
}

/**
 * Start the recommended install and resolve only when the model is usable.
 *
 * The install endpoint only queues background work. Treating its immediate
 * response as success left every ASR consumer showing a stale error and made
 * users hunt through Settings before manually retrying. The shared SSE stream
 * supplies live progress; model-list polling closes the small subscribe/start
 * race when a cached model completes before EventSource receives its terminal
 * event.
 */
export async function installRecommendedAsr(payload, { onProgress, signal } = {}) {
  const rec = payload?.recommended;
  if (!rec?.repo_id) throw new Error(i18next.t('asr_missing.message'));

  let settled = false;
  let pollId = null;
  let abortHandler = null;
  const es = new EventSource(setupDownloadStreamUrl());
  let finish;
  let fail;
  const terminal = new Promise((resolve, reject) => {
    finish = resolve;
    fail = reject;
  });
  const cleanup = () => {
    es.close();
    if (pollId) clearInterval(pollId);
    if (abortHandler) signal?.removeEventListener('abort', abortHandler);
  };
  const resolveOnce = () => {
    if (settled) return;
    settled = true;
    cleanup();
    finish(rec);
  };
  const rejectOnce = (error) => {
    if (settled) return;
    settled = true;
    cleanup();
    fail(error instanceof Error ? error : new Error(String(error)));
  };

  es.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data);
      if (event?.repo_id !== rec.repo_id) return;
      if (event.phase === 'install_done') {
        onProgress?.({ phase: 'ready', percent: 100, event });
        resolveOnce();
        return;
      }
      if (event.phase === 'install_error') {
        const message = event.error || i18next.t('asr_missing.message');
        rejectOnce(new Error(i18next.t('asr_missing.install_failed', { message })));
        return;
      }
      if (event.phase === 'install_cancelled') {
        rejectOnce(new DOMException('Model installation cancelled', 'AbortError'));
        return;
      }
      onProgress?.({ phase: 'installing', percent: installPercent(event), event });
    } catch {
      /* SSE keepalive or malformed progress event */
    }
  };

  if (signal) {
    abortHandler = () => rejectOnce(new DOMException('Model installation cancelled', 'AbortError'));
    if (signal.aborted) abortHandler();
    else signal.addEventListener('abort', abortHandler, { once: true });
  }

  try {
    onProgress?.({ phase: 'installing', percent: 0 });
    await installModel(rec.repo_id);
    // Authoritative fallback for a cached/very fast install whose terminal SSE
    // event raced the subscription. Poll only while this one install is active.
    if (!settled) {
      pollId = setInterval(async () => {
        try {
          const data = await listModels();
          if (data?.models?.some((model) => model.repo_id === rec.repo_id && model.installed)) {
            onProgress?.({ phase: 'ready', percent: 100 });
            resolveOnce();
          }
        } catch {
          /* the SSE stream remains authoritative while the backend reconnects */
        }
      }, 1500);
    }
    const ready = await terminal;
    if (rec.dictation_id) {
      await apiPost('/dictation/prefs', { model_id: rec.dictation_id });
    }
    return ready;
  } catch (error) {
    if (settled) throw error instanceof Error ? error : new Error(String(error));
    rejectOnce(error);
    return terminal;
  }
}

/** Extract the typed payload from any of the transport shapes, or null. */
export function asrMissingPayload(err) {
  if (!err || typeof err !== 'object') return null;
  // Error tagged by the dub SSE handler.
  if (err.asrModelMissing && typeof err.asrModelMissing === 'object') return err.asrModelMissing;
  // Raw SSE data / WS frame.
  if (err.error === ASR_MODEL_MISSING) return err;
  // ApiError from apiFetch: structured 409 detail.
  const d = err.detail;
  if (d && typeof d === 'object' && d.error === ASR_MODEL_MISSING) return d;
  return null;
}

/** Actionable toast: message + one-click download of the recommended model. */
export function toastAsrModelMissing(
  payload,
  { onInstallStart, onProgress, onReady, onError } = {},
) {
  const t = i18next.t.bind(i18next);
  const rec = payload?.recommended;
  const message = t('asr_missing.message');
  if (!rec || !rec.repo_id) {
    toast.error(message, { duration: 8000 });
    return;
  }
  const label = rec.label || rec.repo_id;
  toast.error(
    (tst) => (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ flex: 1 }}>{message}</span>
        <button
          type="button"
          className="btn-secondary"
          style={{ flexShrink: 0, whiteSpace: 'nowrap' }}
          onClick={async () => {
            toast.dismiss(tst.id);
            const progressId = `asr-install:${rec.repo_id}`;
            try {
              onInstallStart?.(rec);
              toast.loading(t('dub.install_progress', { engine: label }), {
                id: progressId,
                duration: Infinity,
              });
              await installRecommendedAsr(payload, {
                onProgress: (state) => {
                  onProgress?.(state, rec);
                  const pct = state.percent == null ? '' : ` ${Math.round(state.percent)}%`;
                  toast.loading(`${t('dub.install_progress', { engine: label })}${pct}`, {
                    id: progressId,
                    duration: Infinity,
                  });
                },
              });
              toast.success(t('dub.install_ok', { engine: label }), {
                id: progressId,
                duration: 5000,
              });
              await onReady?.(rec);
            } catch (e) {
              const error = e instanceof Error ? e : new Error(String(e));
              onError?.(error, rec);
              toast.error(t('asr_missing.install_failed', { message: error.message }), {
                id: progressId,
                duration: 10000,
              });
            }
          }}
        >
          {t('asr_missing.download', { label, size: rec.size_gb })}
        </button>
      </div>
    ),
    { duration: 15000 },
  );
}
