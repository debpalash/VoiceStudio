import { PanelLeftCloseIcon, PanelLeftOpenIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useWorkspaceSidebarState } from './use-workspace-sidebar';

export function SidebarToggle() {
  const { t } = useTranslation();
  const { compact, setOpen } = useWorkspaceSidebarState();
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className="shrink-0 text-foreground/70 hover:text-foreground"
      aria-label={t('clone.toggle_sidebar')}
      title={t('clone.toggle_sidebar')}
      aria-expanded={!compact}
      onClick={() => setOpen(compact)}
    >
      {compact ? <PanelLeftOpenIcon /> : <PanelLeftCloseIcon />}
    </Button>
  );
}
