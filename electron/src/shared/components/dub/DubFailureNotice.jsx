import { useTranslation } from 'react-i18next';
import { ExternalLink, Copy } from 'lucide-react';
import toast from 'react-hot-toast';
import { copyText } from '../../utils/copyText';
import { Button } from '../../ui';
import { openDocsFor, classifyError } from '../../utils/errorDocsMap';

/** plan-04 (#131): actionable failure detail — hint + docs deeplink + a copyable
 *  diagnostic block — shown beneath the error badge when the backend sent a
 *  structured failure. */
function DubFailureNotice({ failure }) {
  const { t } = useTranslation();
  if (!failure) return null;
  const topic = failure.docsTopic || classifyError(failure.reason);
  const copyDiagnostic = async () => {
    try {
      await copyText(failure.diagnostic || failure.reason);
      toast.success(t('dub.diagnostic_copied'));
    } catch {
      toast.error(t('dub.copy_failed'));
    }
  };
  return (
    <div className="flex min-w-0 max-w-full flex-col gap-[4px] mt-[4px] [overflow-wrap:anywhere]">
      {failure.hint && <span className="text-[11px] opacity-[0.85]">{failure.hint}</span>}
      <div className="flex gap-[6px] flex-wrap">
        {topic && (
          <Button variant="subtle" size="sm" onClick={() => openDocsFor(topic)}>
            <ExternalLink size={11} /> {t('dub.open_docs')}
          </Button>
        )}
        {failure.diagnostic && (
          <Button variant="subtle" size="sm" onClick={copyDiagnostic}>
            <Copy size={11} /> {t('dub.copy_diagnostic')}
          </Button>
        )}
      </div>
    </div>
  );
}

export default DubFailureNotice;
