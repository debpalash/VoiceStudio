import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * The detail page's responsive frame (see `.integration-detail-layout` in
 * integrations-page.css). Wide containers: the setup in a main column beside
 * a sticky rail. Narrow containers: one column, with the rail's top (status,
 * checklist) above the setup and its bottom (capabilities, website) below it.
 */
export function IntegrationDetailColumns({
  main,
  railTop,
  railBottom,
}: {
  main: ReactNode;
  railTop?: ReactNode;
  railBottom: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className="integration-detail-layout" data-has-rail-top={Boolean(railTop)}>
      <div className="integration-detail-main">{main}</div>
      <aside className="integration-detail-rail" aria-label={t('integrationCatalog.railLabel')}>
        {railTop && <div className="integration-detail-rail-top">{railTop}</div>}
        <div className="integration-detail-rail-bottom">{railBottom}</div>
      </aside>
    </div>
  );
}
