import { act, cleanup, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError } from '@/lib/api/client';
import { modelLicenceDetailsKey } from '../model-licence-view';
import { useSelection } from './use-engine-select';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn() } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
afterEach(cleanup);

const models = [
  { repo_id: 'test/model', fingerprint: 'model-terms' },
  { repo_id: 'test/tokenizer', fingerprint: 'tokenizer-terms' },
];
const warning = "This model can't be used until you accept its licence.";
const blocked = new ApiError(403, warning, {
  detail: { code: 'model_licence_required', models },
});

function mount() {
  const client = new QueryClient();
  const hook = renderHook(() => useSelection('tts', ['engines']), {
    wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
  });
  const accept = (model: typeof models[number], accepted = true) => act(() => {
    client.setQueryData(modelLicenceDetailsKey(model.repo_id), { acceptance: { ...model, accepted } });
  });
  return { ...hook, accept };
}

it('clears the warning only after both exact licences are accepted, without replaying selection', async () => {
  const { result, accept } = mount();
  const request = vi.fn().mockRejectedValue(blocked);
  await act(() => result.current.run(request));
  expect(result.current.failed).toBe(warning);
  accept({ repo_id: 'unrelated/model', fingerprint: 'other' });
  accept(models[0]);
  expect(result.current.failed).toBe(warning);
  accept({ ...models[1], fingerprint: 'old-terms' });
  expect(result.current.failed).toBe(warning);
  accept(models[1], false);
  expect(result.current.failed).toBe(warning);
  accept(models[1]);
  expect(result.current.failed).toBeNull();
  expect(request).toHaveBeenCalledOnce();
});

it('preserves unrelated selection failures after licence updates', async () => {
  const { result, accept } = mount();
  await act(() => result.current.run(() => Promise.reject(new Error('Out of memory'))));
  for (const model of models) accept(model);
  expect(result.current.failed).toBe('Out of memory');
});

it('clears an already-resolved licence error when its response arrives late', async () => {
  const { result, accept } = mount();
  for (const model of models) accept(model);
  await act(() => result.current.run(() => Promise.reject(blocked)));
  expect(result.current.failed).toBeNull();
});
