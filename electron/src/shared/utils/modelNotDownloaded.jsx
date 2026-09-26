import toast from 'react-hot-toast';
import i18next from 'i18next';
import { apiPost } from '../api/client';

export const MODEL_NOT_DOWNLOADED = 'model_not_downloaded';

export function modelNotDownloadedPayload(err) {
  const detail = err?.detail;
  return detail && typeof detail === 'object' && detail.error === MODEL_NOT_DOWNLOADED
    ? detail
    : null;
}

export function toastModelNotDownloaded(payload) {
  const t = i18next.t.bind(i18next);
  const repoId = payload?.repo_ids?.[0];
  const size = payload?.size_bytes;
  const button =
    size == null
      ? t('model_missing.download')
      : t('model_missing.download_size', { size: `${(size / 1024 ** 3).toFixed(1)} GB` });
  toast.error(
    (item) => (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ flex: 1 }}>
          {t('model_missing.message', { target: payload.target_label })}
          {payload.downloadable === false
            ? ` ${t('model_missing.manual_worker', { target: payload.target_label })}`
            : ''}
        </span>
        {repoId && payload.downloadable !== false && (
          <button
            type="button"
            className="btn-secondary"
            onClick={async () => {
              toast.dismiss(item.id);
              try {
                await apiPost('/models/install', { repo_id: repoId, target: payload.target });
                toast.success(t('model_missing.started', { target: payload.target_label }));
              } catch (error) {
                toast.error(
                  t('model_missing.failed', { message: error?.message || String(error) }),
                );
              }
            }}
          >
            {button}
          </button>
        )}
      </div>
    ),
    { duration: 15000 },
  );
}
