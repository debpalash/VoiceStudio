import React from 'react';
import { useTranslation } from 'react-i18next';
import WorkspaceHistory from './WorkspaceHistory';

export default function DubWorkspaceSidebar({
  dubHistory = [],
  restoreDubHistory,
  deleteHistory,
  clearHistory,
}) {
  const { t } = useTranslation();
  return (
    <div className="dub-project-library flex min-h-0 flex-1 flex-col">
      <WorkspaceHistory
        variant="dub"
        title={t('projects.title')}
        clearLabel={t('transcriptions.clear_title')}
        dubHistory={dubHistory}
        restoreDubHistory={restoreDubHistory}
        deleteHistory={deleteHistory}
        clearHistory={clearHistory}
      />
    </div>
  );
}
