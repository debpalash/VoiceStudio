import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import en from '@/i18n/locales/en.json';
import {
  MODEL_LICENCE_REQUIRED_EVENT,
  modelLicenceRequirements,
  type ModelLicenceRequirement,
} from './model-license-contract';

const mock = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', () => {
  class ApiError extends Error {
    constructor(
      readonly status: number,
      readonly detail: string,
      readonly payload: unknown = null,
    ) {
      super(detail);
    }
  }
  return { apiJson: mock.api, ApiError };
});
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      const value = key.split('.').reduce((acc: any, part) => acc?.[part], en);
      if (typeof value !== 'string') return (options?.defaultValue as string) ?? key;
      return value.replace(/\{\{(\w+)\}\}/g, (_, name) => String(options?.[name] ?? ''));
    },
    i18n: { language: 'en' },
  }),
}));
vi.mock('@/components/external-link', () => ({
  ExternalLink: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));
import { ApiError } from '@/lib/api/client';
import { LicenceAcceptanceFooter, ModelLicenceGate, type AcceptableModel } from './model-licence-acceptance';

const gated: ModelLicenceRequirement = {
  repo_id: 'test/noncommercial',
  license: 'CC-BY-NC-4.0',
  category: 'noncommercial',
  fingerprint: `v1:${'a'.repeat(64)}`,
};

let host: HTMLDivElement;
let root: Root;
const text = () => document.body.textContent ?? '';
const button = (name: string) =>
  Array.from(document.querySelectorAll('button')).find((item) => item.textContent === name);
async function render(node: React.ReactNode) {
  const client = new QueryClient();
  await act(async () =>
    root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>),
  );
}

beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  mock.api.mockReset();
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

describe('model licence acceptance', () => {
  it('parses only well-formed model_licence_required errors', () => {
    expect(
      modelLicenceRequirements({ detail: { code: 'model_licence_required', models: [gated] } }),
    ).toEqual([gated]);
    expect(modelLicenceRequirements({ detail: { code: 'other', models: [gated] } })).toBeNull();
    expect(
      modelLicenceRequirements({ detail: { code: 'model_licence_required', models: [{}] } }),
    ).toBeNull();
    expect(modelLicenceRequirements('nope')).toBeNull();
  });

  const pending = (repo: string, label = repo): AcceptableModel => ({
    repo_id: repo,
    label,
    fingerprint: `v2:${repo.length.toString(16).padStart(64, '0')}`,
    required: true,
    accepted: false,
  });
  const boxes = () => Array.from(document.querySelectorAll('input[type="checkbox"]')) as HTMLInputElement[];

  it('states VoiceStudio is not the licensor and needs a ticked box per model', async () => {
    await render(<LicenceAcceptanceFooter models={[pending('a/one', 'One'), pending('b/two', 'Two')]} onClose={() => {}} />);
    expect(text()).toContain(en.modelLicense.acceptNotice);
    expect(boxes().map((box) => box.closest('label')?.textContent)).toEqual([
      en.modelLicense.acceptCheckbox.replace('{{model}}', 'One'),
      en.modelLicense.acceptCheckbox.replace('{{model}}', 'Two'),
    ]);
    const acceptAll = button(en.modelLicense.acceptAll.replace('{{count}}', '2'))!;
    expect(acceptAll.disabled).toBe(true);
    await act(async () => boxes()[0].click());
    expect(acceptAll.disabled).toBe(true); // one box is not enough
    await act(async () => boxes()[1].click());
    expect(acceptAll.disabled).toBe(false);
  });

  it('records each model separately with the fingerprint it showed', async () => {
    const onAccepted = vi.fn();
    mock.api.mockResolvedValue({ accepted: true });
    const models = [pending('a/one', 'One'), pending('bb/two', 'Two')];
    await render(<LicenceAcceptanceFooter models={models} onAccepted={onAccepted} onClose={() => {}} />);
    for (const box of boxes()) await act(async () => box.click());
    await act(async () => button(en.modelLicense.acceptAll.replace('{{count}}', '2'))!.click());
    expect(mock.api.mock.calls.map(([path, init]) => [path, JSON.parse(init.body)])).toEqual(
      models.map((m) => ['/models/licenses/accept', { repo_id: m.repo_id, fingerprint: m.fingerprint, accepted: true }]),
    );
    expect(onAccepted).toHaveBeenCalledOnce();
  });

  it('asks to review again when the terms changed', async () => {
    const onAccepted = vi.fn();
    mock.api.mockRejectedValue(new ApiError(409, 'x', { detail: { code: 'terms_changed' } }));
    await render(<LicenceAcceptanceFooter models={[pending('a/one')]} onAccepted={onAccepted} onClose={() => {}} />);
    await act(async () => boxes()[0].click());
    await act(async () => button(en.modelLicense.accept)!.click());
    expect(text()).toContain(en.modelLicense.acceptChanged);
    expect(onAccepted).not.toHaveBeenCalled();
  });

  it('can withdraw an acceptance', async () => {
    mock.api.mockResolvedValue({ accepted: false });
    await render(
      <LicenceAcceptanceFooter models={[{ ...pending(gated.repo_id), accepted: true }]} onClose={() => {}} />,
    );
    expect(boxes()).toHaveLength(0);
    await act(async () => button(en.modelLicense.revoke)!.click());
    expect(mock.api).toHaveBeenCalledWith('/models/licenses/revoke', {
      method: 'POST',
      body: JSON.stringify({ repo_id: gated.repo_id }),
    });
  });

  it('shows the full licence of every blocked model when another page reports it', async () => {
    const second = { ...gated, repo_id: 'test/tokenizer', license: 'NOASSERTION', category: 'unknown' };
    mock.api.mockImplementation(async (path: string) => {
      const repo = path.replace('/models/licenses/details/', '');
      const model = repo === gated.repo_id ? gated : second;
      return {
        repo_id: repo,
        info: {
          registry_version: 'synthetic',
          registry_digest: 'd'.repeat(64),
          license: model.license,
          credit: `Holder of ${repo}`,
          license_category: model.category,
          review_status: 'unreviewed',
          commercial_inference: 'unknown',
          commercial_outputs: 'unknown',
          component_closure: 'incomplete',
          readiness: 'not_verified',
          blockers: [],
          enforcement: 'disclosure_only',
          evidence_url: `https://example.invalid/${repo}`,
        },
        acceptance: { ...model, required: true, accepted: false, state: 'not_accepted' },
        history: [],
      };
    });
    await render(<ModelLicenceGate />);
    expect(text()).not.toContain(en.modelLicense.requiredTitle);
    await act(async () => {
      window.dispatchEvent(new CustomEvent(MODEL_LICENCE_REQUIRED_EVENT, { detail: [gated, second] }));
    });
    await vi.waitFor(() => expect(text()).toContain('Holder of test/tokenizer'));
    expect(text()).toContain(en.modelLicense.requiredTitle);
    expect(text()).toContain('Holder of test/noncommercial');
    expect(text()).toContain(en.modelLicense.sectionAllows);
    expect(document.querySelector('a[href="https://example.invalid/test/tokenizer"]')).not.toBeNull();
    expect(boxes()).toHaveLength(2);
  });
});
