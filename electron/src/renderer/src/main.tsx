import './lib/random-uuid';
import { SiteBrowserHost } from './features/browser/site-browser-host';
import { installConsoleCapture } from '@shared/utils/consoleBuffer';
import { lazy, StrictMode, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/inter';
import './styles/globals.css';
import './i18n';
import './hooks/use-appearance';
import { App } from './app';
import { installPreloadRecovery } from './lib/preload-recovery';
import { installGlobalErrorRecovery } from './lib/global-error-recovery';
import { installForeignDomGuard } from './lib/foreign-dom-guard';

installForeignDomGuard();
installConsoleCapture();
installGlobalErrorRecovery();
installPreloadRecovery();

const CaptureWidget = lazy(() =>
  import('./features/transcriptions/capture-widget').then((m) => ({ default: m.CaptureWidget })),
);

createRoot(document.getElementById('root')!).render(
  window.location.hash === '#/capture' ? (
    <Suspense fallback={null}>
      <CaptureWidget />
    </Suspense>
  ) : (
    <StrictMode>
      <SiteBrowserHost>
        <App />
      </SiteBrowserHost>
    </StrictMode>
  ),
);
