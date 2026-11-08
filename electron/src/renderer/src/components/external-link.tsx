import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLinkIcon } from 'lucide-react';
import { buttonVariants } from './ui/button';
import { getBridge } from './bridge';
export function ExternalLink({
  href,
  children,
  showIcon = true,
  variant = 'outline',
}: {
  href: string;
  children: React.ReactNode;
  showIcon?: boolean;
  variant?: 'outline' | 'ghost' | 'secondary';
}) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  return (
    <>
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className={buttonVariants({ variant, size: 'sm' })}
        onClick={(event) => {
          const bridge = getBridge();
          if (!bridge) return;
          event.preventDefault();
          setFailed(false);
          void bridge.files.openExternal(href).catch(() => setFailed(true));
        }}
      >
        {children}
        {showIcon && <ExternalLinkIcon />}
      </a>
      {failed && (
        <span role="alert" className="text-sm text-destructive">
          {t('common.error')}
        </span>
      )}
    </>
  );
}
