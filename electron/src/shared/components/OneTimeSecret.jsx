/**
 * OneTimeSecret — the "copy this now, it is shown once" block, with a QR.
 *
 * Both halves of Remote workers hand the user a long opaque string exactly
 * once: an enrollment token (this machine → the worker) and a connection
 * string (the GPU machine → the person connecting). Typing either by hand is
 * not realistic, and the two machines are frequently not the same machine you
 * can paste between — which is precisely the case a QR solves: point the other
 * device's camera at it, or scan it into a phone and forward it.
 *
 * The QR encodes the secret verbatim, so whatever scans it gets the same
 * string the Copy button gives. Long strings drop to error-correction level L
 * — the extra redundancy of M buys nothing on a screen a foot away, and the
 * denser modules would push a ~400-character connection string past what a
 * laptop webcam resolves.
 *
 * Rendering is deliberately best-effort: if QRCode throws (a string past the
 * format's capacity), the block still shows the code and Copy. A missing QR
 * must never take the only copy of a one-time secret with it.
 */
import React, { useEffect, useState } from 'react';
import QRCode from 'qrcode';
import { Check, Copy, QrCode } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { copyText } from '../utils/copyText';

/** mm:ss left, or null when there is no deadline / it has passed. */
function useCountdown(expiresAt) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!expiresAt) return undefined;
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [expiresAt]);
  if (!expiresAt) return null;
  const left = Math.max(0, Math.round(expiresAt - now));
  return {
    expired: left <= 0,
    label: `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`,
  };
}

function useQrDataUrl(value, size = 320) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!value) {
      setUrl('');
      return undefined;
    }
    let cancelled = false;
    // Drop the previous code FIRST. Encoding is async, so leaving it up meant
    // a window where the QR on screen encoded the old secret while the text
    // beside it showed the new one — and the QR is the half people scan.
    setUrl('');
    QRCode.toDataURL(value, {
      margin: 1,
      width: size,
      errorCorrectionLevel: value.length > 200 ? 'L' : 'M',
    })
      .then((data) => {
        if (!cancelled) setUrl(data);
      })
      .catch(() => {
        if (!cancelled) setUrl('');
      });
    return () => {
      cancelled = true;
    };
  }, [value, size]);
  return url;
}

/**
 * @param value      the secret itself — copied and encoded verbatim
 * @param headline   the shown-once warning (already translated)
 * @param note       optional smaller line under the code
 * @param expiresAt  optional epoch seconds; renders a live countdown
 * @param onDone     optional dismiss handler; renders a Done button
 * @param qrSize     rendered QR edge in px (default 132; 96 in the footer)
 */
export default function OneTimeSecret({
  value,
  headline,
  note,
  expiresAt,
  onDone,
  qrSize = 132,
  className = '',
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const qr = useQrDataUrl(value, qrSize * 2);
  const countdown = useCountdown(expiresAt);

  useEffect(() => {
    if (!copied) return undefined;
    const id = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(id);
  }, [copied]);

  const copy = async () => setCopied(await copyText(value));

  return (
    <div
      className={`rounded-lg bg-amber-500/5 p-3 ${className}`.trim()}
      data-slot="one-time-secret"
    >
      {/* Deliberately plain visible text, not an InfoHint tooltip: a warning
          the user must act on before navigating away cannot be hidden behind a
          hover. */}
      <p className="m-0 text-xs text-amber-300">{headline}</p>

      <div className="mt-2 flex flex-wrap items-start gap-3">
        {qr && (
          <img
            src={qr}
            width={qrSize}
            height={qrSize}
            style={{ width: qrSize, height: qrSize }}
            className="block shrink-0 rounded-md bg-white p-1"
            alt={t('settings.workers_qr_alt', {
              defaultValue: 'QR code carrying this code — scan it from the other machine',
            })}
          />
        )}
        <div className="flex min-w-[180px] flex-1 flex-col gap-2">
          <code
            className="max-h-[92px] overflow-auto rounded bg-black/20 p-2 text-xs break-all select-all"
            translate="no"
          >
            {value}
          </code>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={copy}
              className="inline-flex items-center gap-1.5 rounded border-0 bg-white/8 px-2.5 py-1 text-xs cursor-pointer hover:bg-white/12"
            >
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied
                ? t('settings.workers_copied', { defaultValue: 'Copied' })
                : t('settings.workers_copy', { defaultValue: 'Copy' })}
            </button>
            {onDone && (
              <button
                type="button"
                onClick={onDone}
                className="rounded border-0 bg-transparent px-2 py-1 text-xs opacity-70 cursor-pointer hover:opacity-100"
              >
                {t('settings.workers_secret_done', { defaultValue: 'Done' })}
              </button>
            )}
            {countdown && (
              <span
                className={`ml-auto text-[11px] tabular-nums ${
                  countdown.expired ? 'text-red-400' : 'opacity-70'
                }`}
              >
                {countdown.expired
                  ? t('settings.workers_token_expired', {
                      defaultValue: 'Expired — generate a new one',
                    })
                  : t('settings.workers_token_expires_in', {
                      defaultValue: 'Expires in {{time}}',
                      time: countdown.label,
                    })}
              </span>
            )}
          </div>
          {note && (
            <p className="m-0 flex items-start gap-1.5 text-[11px] opacity-70">
              <QrCode size={12} className="mt-[2px] shrink-0" aria-hidden="true" />
              <span>{note}</span>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
