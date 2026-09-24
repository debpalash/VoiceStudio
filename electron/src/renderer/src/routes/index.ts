import { createRootRoute, createRoute, lazyRouteComponent, redirect } from '@tanstack/react-router';
import { AppShell } from '@/components/app-shell/app-shell';
import { readLastSettingsRoute } from '@/lib/settings-route';

const HomePage = lazyRouteComponent(() => import('@/features/home/home-page'), 'HomePage');

const ClonePage = lazyRouteComponent(() => import('@/features/clone/clone-page'), 'ClonePage');
const DesignPage = lazyRouteComponent(() => import('@/features/design/design-page'), 'DesignPage');
const DubPage = lazyRouteComponent(() => import('@/features/dub/dub-page'), 'DubPage');
const BatchPage = lazyRouteComponent(() => import('@/features/batch/batch-page'), 'BatchPage');
const GalleryPage = lazyRouteComponent(
  () => import('@/features/gallery/gallery-page'),
  'GalleryPage',
);
const TranscriptionsPage = lazyRouteComponent(
  () => import('@/features/transcriptions/transcriptions-page'),
  'TranscriptionsPage',
);
const StoriesPage = lazyRouteComponent(
  () => import('@/features/longform/longform-page'),
  'StoriesPage',
);
const AudiobookPage = lazyRouteComponent(
  () => import('@/features/longform/longform-page'),
  'AudiobookPage',
);
const ProjectsPage = lazyRouteComponent(
  () => import('@/features/projects/projects-page'),
  'ProjectsPage',
);
const CallsPage = lazyRouteComponent(() => import('@/features/calls/calls-page'), 'CallsPage');
const ToolsPage = lazyRouteComponent(() => import('@/features/tools/tools-page'), 'ToolsPage');
const IntegrationsPage = lazyRouteComponent(
  () => import('@/features/integrations/integrations-page'),
  'IntegrationsPage',
);
const IntegrationDetailPage = lazyRouteComponent(
  () => import('@/features/integrations/integration-detail-page'),
  'IntegrationDetailPage',
);
const SettingsPage = lazyRouteComponent(
  () => import('@/features/settings/settings-page'),
  'SettingsPage',
);

export const rootRoute = createRootRoute({ component: AppShell });

export const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: HomePage,
});

export const savedVoicesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/personas',
  component: lazyRouteComponent(
    () => import('@/features/clone/saved-voices-page'),
    'SavedVoicesPage',
  ),
});

export const cloneRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/clone',
  component: ClonePage,
});

export const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
  beforeLoad: () => {
    throw redirect({ to: readLastSettingsRoute() });
  },
});

export const appearanceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/appearance',
  component: SettingsPage,
});
export const generalRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/general',
  component: SettingsPage,
});

export const modelsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/models',
  component: SettingsPage,
});
export const modelFamilyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/models/$family',
  component: SettingsPage,
});

export const catchAllRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '$',
  beforeLoad: () => {
    throw redirect({ to: '/' });
  },
});

export const transcriptionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/transcriptions',
  component: TranscriptionsPage,
});

export const designRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/design',
  component: DesignPage,
});

export const dubRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/dub',
  component: DubPage,
});

export const logsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/logs',
  component: SettingsPage,
});

export const galleryRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/gallery',
  component: GalleryPage,
});

export const batchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/batch',
  component: BatchPage,
});
export const callsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/calls',
  component: CallsPage,
});
export const toolsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/tools',
  component: ToolsPage,
});
export const integrationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/integrations',
  component: IntegrationsPage,
});
export const integrationDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/integrations/$slug',
  component: IntegrationDetailPage,
});
export const mediaRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/media',
  component: SettingsPage,
});
export const pronunciationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/pronunciation',
  component: SettingsPage,
});
export const projectsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/projects',
  component: ProjectsPage,
});
export const storiesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/stories',
  component: StoriesPage,
});
export const audiobookRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/audiobook',
  component: AudiobookPage,
});
export const networkRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/network',
  component: SettingsPage,
});
export const sharingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/sharing',
  component: SettingsPage,
});
export const credentialsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/credentials',
  component: SettingsPage,
});
export const performanceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/performance',
  component: SettingsPage,
});
export const usageRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/usage',
  component: SettingsPage,
});
export const workersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/workers',
  component: SettingsPage,
});
export const privacyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/privacy',
  component: SettingsPage,
});
export const permissionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/permissions',
  component: SettingsPage,
});
export const storageRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/storage',
  component: SettingsPage,
});
export const supportRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/support',
  validateSearch: (search: Record<string, unknown>): { compare?: boolean } => ({
    compare: search.compare === true || search.compare === 'true' ? true : undefined,
  }),
  component: SettingsPage,
});
export const updatesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/updates',
  component: SettingsPage,
});
export const openApiRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/openapi',
  component: SettingsPage,
});
export const diagnosticsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/diagnostics',
  component: SettingsPage,
});
export const routeTree = rootRoute.addChildren([
  diagnosticsRoute,
  openApiRoute,
  updatesRoute,
  supportRoute,
  storageRoute,
  privacyRoute,
  permissionsRoute,
  performanceRoute,
  usageRoute,
  workersRoute,
  networkRoute,
  sharingRoute,
  credentialsRoute,
  storiesRoute,
  audiobookRoute,
  projectsRoute,
  indexRoute,
  cloneRoute,
  savedVoicesRoute,
  pronunciationRoute,
  mediaRoute,
  toolsRoute,
  callsRoute,
  integrationsRoute,
  integrationDetailRoute,
  batchRoute,
  galleryRoute,
  logsRoute,
  dubRoute,
  designRoute,
  transcriptionsRoute,
  settingsRoute,
  appearanceRoute,
  generalRoute,
  modelsRoute,
  modelFamilyRoute,
  catchAllRoute,
]);
