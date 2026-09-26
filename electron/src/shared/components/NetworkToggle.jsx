// electron/src/shared/components/NetworkToggle.jsx
import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { copyText } from '../utils/copyText';
import QRCode from 'qrcode';
import { Wifi, WifiOff, Copy, ExternalLink } from 'lucide-react';
import toast from 'react-hot-toast';
import { apiJson, apiPost } from '../api/client';
import { openExternal } from '../api/external';

export default function NetworkToggle() {
  const { t } = useTranslation();
  const [st, setSt] = useState({ enabled: false });
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [qrs, setQrs] = useState({});

  const refresh = useCallback(async () => {
    try {
      setSt(await apiJson('/system/network/state'));
    } catch {
      /* loopback only; ignore */
    }
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!st.enabled || !st.pin) {
      setQrs({});
      return;
    }
    let cancelled = false;
    (async () => {
      const next = {};
      for (const ip of st.lan_addresses || []) {
        next[ip] = await QRCode.toDataURL(`http://${ip}:${st.share_port}/?pin=${st.pin}`);
      }
      if (!cancelled) setQrs(next);
    })();
    return () => {
      cancelled = true;
    };
  }, [st.enabled, st.pin, st.share_port, st.lan_addresses]);

  // NOTE: do NOT use window.confirm here — it's a no-op in the Tauri webview
  // (returns false), which silently swallowed the enable action.
  const enable = async () => {
    setBusy(true);
    try {
      setSt(await apiPost('/system/network/enable'));
      setConfirming(false);
      setOpen(true);
    } catch (e) {
      toast.error(t('network.enable_error', { message: e.message }));
    } finally {
      setBusy(false);
    }
  };
  const disable = async () => {
    setBusy(true);
    try {
      await apiPost('/system/network/disable');
      await refresh();
      setOpen(false);
    } catch (e) {
      toast.error(t('network.disable_error', { message: e.message }));
    } finally {
      setBusy(false);
    }
  };

  const copy = (text) => {
    copyText(text);
    toast.success(t('network.copied'));
  };

  return (
    <div className="relative inline-flex items-center flex-shrink-0">
      <button
        className={`inline-flex items-center gap-[5px] py-[2px] px-[8px] h-[20px] rounded-sm font-medium text-[11px] [font-family:inherit] cursor-pointer [transition:all_0.1s] border border-solid disabled:opacity-50 disabled:cursor-not-allowed ${st.enabled ? 'bg-[rgba(184,187,38,0.12)] border-transparent text-[#b8bb26] hover:bg-[rgba(184,187,38,0.18)]' : 'bg-transparent border-transparent text-[var(--color-fg-muted)] hover:bg-[var(--chrome-hover-bg)] hover:text-fg'}`}
        onClick={st.enabled ? () => setOpen((o) => !o) : () => setConfirming((c) => !c)}
        disabled={busy}
        title={st.enabled ? t('network.sharing_on_title') : t('network.share_on_network')}
      >
        {/* 14px matches the footer's other right-side icons (Discord, Mail,
            donate heart) — keep them optically uniform. */}
        {st.enabled ? <Wifi size={14} /> : <WifiOff size={14} />}
        <span>
          {busy ? t('network.switching') : st.enabled ? t('network.network') : t('network.local')}
        </span>
      </button>

      {!st.enabled && confirming && (
        <div className="absolute bottom-[calc(100%+8px)] right-0 z-[60] w-[248px] flex flex-col gap-[8px] p-[12px] bg-[var(--chrome-bg,#1d2021)] border border-solid border-transparent rounded-[8px] shadow-[0_8px_24px_rgba(0,0,0,0.45)] text-fg">
          <div className="text-[11px] font-semibold uppercase [letter-spacing:0.06em] text-[#b8bb26]">
            {t('network.share_confirm_title')}
          </div>
          <p className="m-0 text-[11px] [line-height:1.5] text-[#a89984]">
            {t('network.share_confirm_hint')}
          </p>
          <div className="flex gap-[6px] mt-[8px]">
            <button
              type="button"
              className="bg-transparent border border-solid border-transparent [color:inherit] text-[11px] [font-family:inherit] py-[5px] px-[12px] rounded-md cursor-pointer hover:bg-[var(--chrome-hover-bg)] disabled:opacity-50 disabled:cursor-not-allowed"
              onClick={() => setConfirming(false)}
              disabled={busy}
            >
              {t('common.cancel')}
            </button>
            <button
              type="button"
              className="bg-[rgba(184,187,38,0.15)] border border-solid border-transparent text-[#b8bb26] text-[11px] font-semibold [font-family:inherit] py-[5px] px-[12px] rounded-md cursor-pointer hover:bg-[rgba(184,187,38,0.25)] disabled:opacity-50 disabled:cursor-not-allowed"
              onClick={enable}
              disabled={busy}
            >
              {busy ? t('network.enabling') : t('network.enable')}
            </button>
          </div>
        </div>
      )}

      {st.enabled && open && (
        <div className="absolute bottom-[calc(100%+8px)] right-0 z-[60] w-[248px] flex flex-col gap-[8px] p-[12px] bg-[var(--chrome-bg,#1d2021)] border border-solid border-transparent rounded-[8px] shadow-[0_8px_24px_rgba(0,0,0,0.45)] text-fg">
          <div className="text-[11px] font-semibold uppercase [letter-spacing:0.06em] text-[#b8bb26]">
            {t('network.shared_title')}
          </div>
          {(st.lan_addresses || []).length === 0 && (
            <p className="m-0 text-[11px] [line-height:1.5] text-[#a89984]">
              {t('network.no_interface')}
            </p>
          )}
          {(st.lan_addresses || []).map((ip) => {
            const url = `http://${ip}:${st.share_port}/?pin=${st.pin}`;
            return (
              <div
                key={ip}
                className="flex flex-col items-center gap-[8px] p-[8px] rounded-lg bg-[rgba(255,255,255,0.03)] border border-solid border-transparent"
              >
                <div className="flex items-center justify-between gap-[8px] w-full">
                  <code className="[font-family:var(--chrome-font-mono,var(--font-mono,monospace))] text-[11.5px] text-fg break-all">
                    {ip}:{st.share_port}
                  </code>
                  <div className="flex items-center gap-[2px] flex-shrink-0">
                    <button
                      type="button"
                      className="bg-transparent border-0 text-[#a89984] cursor-pointer w-[22px] h-[22px] rounded-sm flex items-center justify-center [transition:all_0.1s] hover:text-[#b8bb26] hover:bg-[rgba(255,255,255,0.06)]"
                      onClick={() => copy(url)}
                      aria-label={`Copy ${ip}`}
                      title={t('network.copy_link')}
                    >
                      <Copy size={12} />
                    </button>
                    <button
                      type="button"
                      className="bg-transparent border-0 text-[#a89984] cursor-pointer w-[22px] h-[22px] rounded-sm flex items-center justify-center [transition:all_0.1s] hover:text-[#b8bb26] hover:bg-[rgba(255,255,255,0.06)]"
                      onClick={() => openExternal(url)}
                      aria-label={`Open ${ip}`}
                      title={t('network.open_in_browser')}
                    >
                      <ExternalLink size={12} />
                    </button>
                  </div>
                </div>
                {qrs[ip] && (
                  <img
                    className="block w-[104px] h-[104px] rounded-md bg-[#fff] p-[4px]"
                    src={qrs[ip]}
                    alt={t('network.qr_alt', { ip })}
                    width={104}
                    height={104}
                  />
                )}
              </div>
            );
          })}
          <div className="text-[12px] text-[#a89984] text-center">
            {t('network.pin')}{' '}
            <strong className="[font-family:var(--chrome-font-mono,var(--font-mono,monospace))] text-[14px] [letter-spacing:0.12em] text-[#b8bb26]">
              {st.pin}
            </strong>
          </div>
          <button
            type="button"
            className="bg-[rgba(251,73,52,0.12)] border border-solid border-transparent text-danger text-[11px] font-semibold [font-family:inherit] py-[5px] px-[10px] rounded-md cursor-pointer [transition:all_0.1s] hover:bg-[rgba(251,73,52,0.2)] disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={disable}
            disabled={busy}
          >
            {t('network.stop_sharing')}
          </button>
        </div>
      )}
    </div>
  );
}
