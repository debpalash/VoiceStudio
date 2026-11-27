import { createHashHistory, createRouter } from '@tanstack/react-router';
import { routeTree } from './routes';
import { ErrorRecovery } from './components/error-boundary';

// Hash history: production loads from app://voicestudio/index.html, where a
// path-based history would resolve routes against the custom scheme.
export const router = createRouter({
  routeTree,
  history: createHashHistory(),
  defaultPreload: 'intent',
  defaultPreloadDelay: 50,
  defaultErrorComponent: ErrorRecovery,
  scrollRestoration: false,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
