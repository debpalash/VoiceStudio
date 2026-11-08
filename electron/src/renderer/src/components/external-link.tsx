import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLinkIcon } from 'lucide-react';
import { buttonVariants } from './ui/button';
import { cn } from '@/lib/utils';
import { getBridge } from './bridge';
export function ExternalLink({
  href,
  children,
  showIcon = true,
  variant = 'outline',
  className,
}: {
  href: string;
  children: React.ReactNode;
  showIcon?: boolean;
  variant?: 'outline' | 'ghost' | 'secondary' | 'link';
  className?: string;
}) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  return (
    <>
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className={cn(buttonVariants({ variant, size: 'sm' }), className)}
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
