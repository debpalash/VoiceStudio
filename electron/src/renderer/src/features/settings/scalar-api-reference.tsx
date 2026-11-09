import { ApiReferenceReact } from '@scalar/api-reference-react';
import '@scalar/api-reference-react/style.css';
import { useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useTheme } from '@/hooks/use-theme';

export default function ScalarApiReference({ spec }: { spec: Record<string, unknown> }) {
  const { t } = useTranslation();
  const { theme } = useTheme();
  const rootRef = useRef<HTMLDivElement>(null);
  const configuration = useMemo(
    () => ({
      content: spec,
      withDefaultFonts: false,
      proxyUrl: '',
      darkMode: theme === 'dark',
      // The app theme drives the reference; Scalar's own unlabelled colour toggle would only
      // fall out of step with it (and screen readers announce it as an unnamed button).
      hideDarkModeToggle: true,
      agent: { disabled: true },
      showDeveloperTools: 'never' as const,
      // Scalar uses the bare URL hash for section navigation by default,
      // which collides with the app's hash router. Keep its document anchors
      // in the pathname so the VoiceStudio route remains mounted.
      pathRouting: { basePath: '/api-reference' },
    }),
    [spec, theme],
  );
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const identifyEmbeddedLandmarks = () => {
      for (const landmark of root.querySelectorAll('main')) {
        landmark.setAttribute('role', 'region');
        landmark.setAttribute('aria-label', t('openapi.title'));
      }
    };
    identifyEmbeddedLandmarks();
    const observer = new MutationObserver(identifyEmbeddedLandmarks);
    observer.observe(root, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [t]);
  return (
    <div ref={rootRef} data-slot="scalar-reference" className="contents">
      <ApiReferenceReact configuration={configuration} />
    </div>
  );
}
