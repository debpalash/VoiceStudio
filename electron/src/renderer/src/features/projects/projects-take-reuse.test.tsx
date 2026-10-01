import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cloneSettingsStore, DEFAULT_CLONE_SETTINGS, patchCloneSettings } from '@/lib/store/clone-settings';
import { rememberTake } from '@/lib/store/takes';
import type { HistoryItem } from '@/lib/api/types';

const mocks = vi.hoisted(() => ({ navigate: vi.fn(), history: [] as HistoryItem[] }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => mocks.navigate }));
vi.mock('lucide-react', () => {
  const Icon = () => null;
  return Object.fromEntries(['AudioLines', 'BookOpen', 'Clock', 'Download', 'FileText', 'Film', 'Fingerprint', 'FolderOpen', 'Grid2X2', 'List', 'Mic', 'Pencil', 'Save', 'Search', 'Trash'].map(name => [name + 'Icon', Icon]));
});
vi.mock('@/components/workspace-sidebar', () => ({ SecondarySidebar: ({ children }: any) => <aside>{children}</aside> }));
vi.mock('@/components/app-shell/workspace-header', () => ({ WorkspaceHeader: ({ children }: any) => <header>{children}</header> }));
vi.mock('@/components/ui/button', () => ({ Button: ({ children, variant: _variant, size: _size, ...props }: any) => <button {...props}>{children}</button> }));
vi.mock('@/components/ui/input', () => ({ Input: (props: any) => <input {...props} /> }));
vi.mock('@/components/ui/dialog', () => ({ Dialog: () => null, DialogContent: () => null, DialogHeader: () => null, DialogTitle: () => null, DialogDescription: () => null, DialogFooter: () => null }));
vi.mock('@/components/audio-preview-button', () => ({ AudioPreviewButton: () => null }));
vi.mock('@/components/pipeline-failure', () => ({ PipelineFailure: () => null }));
vi.mock('@/components/profile-avatar', () => ({ ProfileAvatar: () => null }));
vi.mock('@/components/bridge', () => ({ getBridge: () => null }));
vi.mock('@/hooks/use-profiles', () => ({ useProfiles: () => ({ data: [] }), useDeleteProfile: () => ({ mutateAsync: vi.fn() }) }));
vi.mock('@/hooks/use-history', () => ({ useHistory: () => ({ data: mocks.history }) }));
vi.mock('@/lib/api/client', () => ({ apiJson: async (path: string) => path === '/longform/jobs' ? { jobs: [] } : [], apiPath: (path: string) => path, describeError: String }));
vi.mock('@/lib/global-error-recovery', () => ({ runRendererTask: vi.fn() }));
vi.mock('../dub/dub-session', () => ({ useDubSession: () => ({ phase: 'idle' }), openDubProject: vi.fn(), attachDubProject: vi.fn(), detachDubProject: vi.fn() }));
vi.mock('../longform/longform-session', () => ({ useLongformSession: () => ({ active: false }), longformSession: { state: { active: false, drafts: {} } }, blankLongformDraft: () => ({}), editLongform: vi.fn() }));
vi.mock('../longform/project-library', () => ({ projectLibrary: { list: async () => [], remove: vi.fn(), rename: vi.fn() } }));
vi.mock('./render-details', () => ({ RenderDetails: () => null, renderRecipe: () => null }));
import { ProjectsPage } from './projects-page';

let client: QueryClient;
afterEach(() => { cleanup(); client?.clear(); localStorage.clear(); vi.clearAllMocks(); });
it('restores saved Clone quality through the Projects Reuse button', async () => {
  const item: HistoryItem = { id: 'saved', text: 'Saved take', mode: 'clone', language: 'English', instruct: '', profile_id: 'voice', audio_path: 'saved.wav', duration_seconds: 1, generation_time: 1, seed: null, starred: false, created_at: 1 };
  localStorage.clear();
  mocks.history = [item];
  rememberTake(item.id, { ...DEFAULT_CLONE_SETTINGS, wavBits: 24, effectPreset: 'raw', speed: 1.5 });
  patchCloneSettings({ ...DEFAULT_CLONE_SETTINGS, wavBits: 16, effectPreset: 'broadcast', autoPlay: true });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ProjectsPage /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button', { name: 'clone.history_reuse' }));
  await waitFor(() => expect(mocks.navigate).toHaveBeenCalledWith({ to: '/clone' }));
  expect(cloneSettingsStore.state).toMatchObject({ wavBits: 24, effectPreset: 'raw', speed: 1.5, autoPlay: true, text: 'Saved take', selectedProfileId: 'voice' });
});
