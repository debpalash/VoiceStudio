/**
 * ReportBugButton — opt-in bug reporter via prefilled GitHub Issues URL.
 *
 * Capability 2 from CLAUDE.md. The user clicks → we build a URL like:
 *   https://github.com/{owner}/{repo}/issues/new?title=…&body=…&labels=bug
 * and open it in their browser. They review what we captured and click
 * Submit on github.com if they're happy. We never hold an auth token,
 * never POST to GitHub directly, and never bypass the user's review —
 * opt-in by construction, no separate consent dialog needed.
 *
 * Capture + scrubbing live in utils/bugReport.js (shared with the
 * ErrorBoundary's report action and error toasts): version, OS, GPU/CPU/
 * RAM, active TTS engine — home paths and credential-shaped strings are
 * redacted, audio contents never included.
 */
import { useState } from 'react';
import { Bug } from 'lucide-react';
import { Button } from '../ui';
import { openBugReport } from '../utils/bugReport';
import { useTranslation } from 'react-i18next';

export default function ReportBugButton({ size = 'sm', variant = 'subtle', label, error }) {
  const { t } = useTranslation();
  const displayLabel = label || t('reportBug.label');
  const [building, setBuilding] = useState(false);

  const handleClick = async () => {
    setBuilding(true);
    try {
      await openBugReport({ error });
    } finally {
      setBuilding(false);
    }
  };

  return (
    <Button
      size={size}
      variant={variant}
      onClick={handleClick}
      loading={building}
      leading={!building && <Bug size={12} />}
      title={t('reportBug.title')}
    >
      {displayLabel}
    </Button>
  );
}
