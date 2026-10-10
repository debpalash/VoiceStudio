import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import en from '@/i18n/locales/en.json';
import type { ModelLicenseInfo, ModelReviewPlan } from './model-license-contract';
import {
  canAcknowledgeModelPlan,
  modelLicenseStatusKey,
  modelLicenseUrl,
} from './model-license-contract';

const mock = vi.hoisted(() => ({ api: vi.fn(), details: vi.fn() }));
// Licence details have their own read; the reviewed-install calls stay on `api`.
vi.mock('@/lib/api/client', () => ({
  apiJson: (path: string, init?: unknown) =>
    path.startsWith('/models/licenses/details/') ? mock.details(path) : mock.api(path, init),
  ApiError: class ApiError extends Error {},
}));
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
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ModelLicense } from './model-license';

// Deliberately invented identities and document bytes for UI tests only.
const info: ModelLicenseInfo = {
  registry_version: 'synthetic-test',
  registry_digest: 'b'.repeat(64),
  license: 'LicenseRef-SyntheticTest',
  credit: 'Synthetic test source',
  source_url: 'https://example.invalid/model',
  evidence_url: 'https://example.invalid/pinned',
  evidence_revision: 'a'.repeat(40),
  runtime_revision: 'a'.repeat(40),
  review_status: 'unreviewed',
  commercial_inference: 'unknown',
  commercial_outputs: 'unknown',
  component_closure: 'incomplete',
  readiness: 'not_verified',
  blockers: [],
  enforcement: 'disclosure_only',
};
function syntheticPlan(): ModelReviewPlan {
  return {
    plan_id: 'synthetic-test',
    plan_digest: 'c'.repeat(64),
    registry_digest: info.registry_digest,
    repo_id: 'test/model',
    target: 'local',
    source: 'https://example.invalid',
    readiness: 'ready',
    blockers: [],
    enforcement: 'reviewed_install_only',
    components: [
      {
        id: 'test/model',
        revision: 'a'.repeat(40),
        license: info.license,
        credit: info.credit,
        component_closure: 'complete',
        artifacts: [
          { path: 'test.bin', sha256: 'd'.repeat(64), size_bytes: 1, document_ids: ['test-terms'] },
        ],
      },
    ],
    documents: [
      {
        id: 'test-terms',
        title: 'Synthetic terms',
        text: 'Synthetic test document. Not a real grant.',
        source_url: 'https://example.invalid/terms',
        sha256: 'e'.repeat(64),
        redistribution: 'permitted',
        agreement_required: false,
      },
    ],
    acknowledgement: {
      prompt_id: 'synthetic-notice',
      prompt_version: 1,
      text: 'Synthetic notice only.',
    },
  };
}
let host: HTMLDivElement;
let queryClient: QueryClient;
let root: Root;
const text = () => document.body.textContent ?? '';
const button = (name: string) =>
  Array.from(document.querySelectorAll('button')).find((item) => item.textContent === name)!;
async function click(element: HTMLElement) {
  await act(async () => element.click());
}
async function render(licenseInfo = info, target = 'local') {
  await act(async () =>
    root.render(
      <QueryClientProvider client={queryClient}>
        <ModelLicense repoId="test/model" label="Test model" info={licenseInfo} target={target} />
      </QueryClientProvider>,
    ),
  );
}
async function open() {
  await click(document.querySelector('button')!);
}
beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  mock.api.mockReset();
  mock.details.mockReset();
  mock.details.mockRejectedValue(new Error('details unavailable in this test'));
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

describe('versioned model notices', () => {
  it.each([
    ['commercial', 'unreviewed', en.modelLicense.categoryCommercial, en.modelLicense.notReviewed],
    ['conditions', 'unreviewed', en.modelLicense.categoryConditions, en.modelLicense.notReviewed],
    [
      'noncommercial',
      'noncommercial',
      en.modelLicense.categoryNoncommercial,
      en.modelLicense.notReviewed,
    ],
    ['commercial', 'cleared', en.modelLicense.categoryCommercial, en.modelLicense.reviewed],
    [undefined, 'unreviewed', en.modelLicense.categoryUnknown, en.modelLicense.notReviewed],
    ['permitted', 'unreviewed', en.modelLicense.categoryUnknown, en.modelLicense.notReviewed],
  ])(
    'shows a Licence: icon %s/%s trigger named by licence, category and review state',
    async (category, review, categoryLabel, reviewLabel) => {
      await render({ ...info, license_category: category, review_status: review });
      const summary = `${info.license} · ${categoryLabel} · ${reviewLabel}`;
      const trigger = within(host).getByRole('button', {
        name: `${en.modelLicense.label}: ${summary}; Test model`,
      });
      expect(trigger.textContent).toBe(`${en.modelLicense.label}:`);
      expect(trigger.getAttribute('title')).toBe(summary);
      const svg = trigger.querySelector('svg')!;
      expect(svg.getAttribute('aria-hidden')).toBe('true');
      expect(svg.getAttribute('focusable')).toBe('false');
      expect(svg.getAttribute('data-license-category')).toBe(
        category === 'permitted' || !category ? 'unknown' : category,
      );
    },
  );

  it('leads with the summary and what the licence allows, with details collapsed', async () => {
    await render();
    await open();
    const dialog = document.querySelector('[role="dialog"]')!;
    const body = dialog.textContent ?? '';
    // Summary first, then permissions, then sources.
    const order = [
      en.modelLicense.categoryUnknown,
      en.modelLicense.sectionAllows,
      en.modelLicense.sectionSource,
      en.modelLicense.sectionTechnical,
    ].map((part) => body.indexOf(part));
    expect(order.every((index) => index >= 0)).toBe(true);
    expect([...order].sort((x, y) => x - y)).toEqual(order);
    for (const row of ['permModel', 'permOutputs', 'permRedistribution', 'permVoice'] as const) {
      expect(body).toContain(en.modelLicense[row]);
    }
    expect(body).toContain(en.modelLicense.chipYours);
    expect(document.querySelector('a[href="https://example.invalid/pinned"]')?.textContent).toBe(
      en.modelLicense.readLicence,
    );
    // Technical evidence and the reviewed-install preview start collapsed.
    const sections = Array.from(dialog.querySelectorAll('details')).filter((d) =>
      [en.modelLicense.sectionTechnical, en.modelLicense.sectionVerified].includes(
        d.querySelector('summary')?.textContent ?? '',
      ),
    );
    expect(sections).toHaveLength(2);
    expect(sections.every((section) => !section.open)).toBe(true);
    // Full hashes are not printed inline.
    expect(body).not.toContain('a'.repeat(40));
    expect(mock.api).not.toHaveBeenCalled();
    await click(button(en.common.close));
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
    expect(document.activeElement).toBe(document.querySelector('button'));
  });

  const fingerprint = `v2:${'c'.repeat(64)}`;
  const details = (acceptance: Record<string, unknown>, history: unknown[] = []) => ({
    repo_id: 'test/model',
    info: { ...info, license_category: 'noncommercial' },
    acceptance: {
      repo_id: 'test/model',
      license: info.license,
      category: 'noncommercial',
      required: true,
      fingerprint,
      ...acceptance,
    },
    history,
  });

  it('records acceptance through one checkbox and shows the stored time and hash', async () => {
    const acceptedAt = '2026-10-10T09:00:00+00:00';
    mock.details.mockResolvedValue(details({ accepted: false, state: 'not_accepted' }));
    mock.api.mockResolvedValue({ accepted: true });
    await render();
    await open();
    await vi.waitFor(() => expect(text()).toContain(en.modelLicense.stateNotAccepted));
    const accept = button(en.modelLicense.accept);
    expect(accept.disabled).toBe(true);
    const box = document.querySelector('[role="dialog"] input[type="checkbox"]') as HTMLInputElement;
    expect(box.closest('label')?.textContent).toBe(
      en.modelLicense.acceptCheckbox.replace('{{model}}', 'Test model'),
    );
    mock.details.mockResolvedValue(
      details(
        { accepted: true, state: 'accepted', accepted_at: acceptedAt, last_action: { action: 'accepted', at: acceptedAt, fingerprint } },
        [{ action: 'accepted', at: acceptedAt, fingerprint, app_version: '0.5.7' }],
      ),
    );
    await click(box);
    await click(accept);
    expect(mock.api).toHaveBeenCalledWith('/models/licenses/accept', {
      method: 'POST',
      body: JSON.stringify({ repo_id: 'test/model', fingerprint, accepted: true }),
    });
    await vi.waitFor(() => expect(text()).toContain('Accepted Oct 10, 2026'));
    expect(text()).toContain('v2:cccccc…cccc');
    expect(text()).toContain(en.modelLicense.history);
    expect(button(en.modelLicense.revoke)).toBeDefined();
  });

  it.each([
    ['withdrawn', { accepted: false, state: 'withdrawn', last_action: { action: 'withdrawn', at: '2026-10-11T08:00:00+00:00', fingerprint } }, 'Acceptance withdrawn Oct 11, 2026'],
    [
      'terms_updated',
      { accepted: false, state: 'terms_updated', accepted_at: '2026-10-10T09:00:00+00:00', changed_fields: ['notes', 'documents'] },
      'The licence terms changed after you accepted them on Oct 10, 2026',
    ],
  ])('explains the %s state and asks again', async (_state, acceptance, message) => {
    mock.details.mockResolvedValue(details(acceptance));
    await render();
    await open();
    await vi.waitFor(() => expect(text()).toContain(message));
    if (_state === 'terms_updated') {
      expect(text()).toContain(
        en.modelLicense.changedFields.replace(
          '{{fields}}',
          `${en.modelLicense.field.notes}, ${en.modelLicense.field.documents}`,
        ),
      );
    }
    expect(button(en.modelLicense.accept)).toBeDefined();
  });

  it('never offers acknowledgement for a real-style incomplete plan', async () => {
    mock.api.mockResolvedValue({
      ...syntheticPlan(),
      readiness: 'blocked',
      blockers: ['terms_unverified'],
      documents: [],
    });
    await render();
    await open();
    await click(button(en.modelLicense.prepare));
    expect(text()).toContain(en.modelLicense.blocked);
    expect(text()).toContain(en.modelLicense.blockerTerms);
    expect(document.querySelector('input[type="checkbox"]')).toBeNull();
    expect(button(en.modelLicense.commit)).toBeUndefined();
    expect(mock.api.mock.calls.map(([path]) => path)).toEqual(['/models/install/prepare']);
  });

  it('ignores preparation completing after Close and reopen', async () => {
    let resolve!: (plan: ModelReviewPlan) => void;
    mock.api.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    await render();
    await open();
    await click(button(en.modelLicense.prepare));
    await click(button(en.common.close));
    await open();
    await act(async () => resolve(syntheticPlan()));
    expect(document.querySelector('input[type="checkbox"]')).toBeNull();
    expect(text()).not.toContain('Synthetic notice only.');
  });

  it('clears a prepared plan when registry or selected target changes', async () => {
    mock.api.mockResolvedValue(syntheticPlan());
    await render();
    await open();
    await click(button(en.modelLicense.prepare));
    await click(document.querySelector('input')!);
    await render({ ...info, registry_digest: 'f'.repeat(64) });
    expect(document.querySelector('input[type="checkbox"]')).toBeNull();
    await render(info, 'worker:test');
    expect(button(en.modelLicense.prepare).disabled).toBe(true);
    expect(text()).toContain('worker:test');
  });

  it('requires an explicit checkbox and commits the exact document set once', async () => {
    mock.api.mockResolvedValueOnce(syntheticPlan());
    let resolve!: (value: { status: string }) => void;
    mock.api.mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    await render();
    await open();
    await click(button(en.modelLicense.prepare));
    const commit = button(en.modelLicense.commit);
    expect(commit.disabled).toBe(true);
    await click(document.querySelector('input')!);
    await act(async () => {
      commit.click();
      commit.click();
    });
    expect(mock.api).toHaveBeenCalledTimes(2);
    expect(mock.api.mock.calls[1][0]).toBe('/models/install/commit');
    expect(JSON.parse(mock.api.mock.calls[1][1].body)).toEqual({
      plan_id: 'synthetic-test',
      plan_digest: 'c'.repeat(64),
      acknowledged_document_ids: ['test-terms'],
      acknowledged: true,
      target: 'local',
    });
    expect(button(en.common.close).disabled).toBe(true);
    await act(async () => resolve({ status: 'verified' }));
    expect(text()).toContain(en.modelLicense.downloaded);
    expect(document.querySelector('input[type="checkbox"]')).toBeNull();
    expect(
      mock.api.mock.calls.some(
        ([path]) => path === '/api/settings/license' || path === '/models/install',
      ),
    ).toBe(false);
  });

  it('requires a new plan and acknowledgement after a failed commit', async () => {
    mock.api
      .mockResolvedValueOnce(syntheticPlan())
      .mockRejectedValueOnce(new Error('plan_changed'));
    await render();
    await open();
    await click(button(en.modelLicense.prepare));
    await click(document.querySelector('input')!);
    await click(button(en.modelLicense.commit));
    expect(text()).toContain(en.modelLicense.failed);
    expect(document.querySelector('input[type="checkbox"]')).toBeNull();
    expect(button(en.modelLicense.prepare).disabled).toBe(false);
  });

  it('renders original documents as inert text and rejects unsafe source links', async () => {
    const plan = syntheticPlan();
    plan.documents[0].text = '<img src=x onerror=alert(1)>';
    plan.documents[0].source_url = 'javascript:alert(1)';
    mock.api.mockResolvedValue(plan);
    await render({ ...info, source_url: 'file:///private', evidence_url: 'data:text/html,unsafe' });
    await open();
    await click(button(en.modelLicense.prepare));
    expect(text()).toContain('<img src=x onerror=alert(1)>');
    expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('a')).toBeNull();
  });
});

describe('conservative model notice contract', () => {
  it('never derives commercial permission from an unknown status or engine licence', () => {
    for (const status of [
      undefined,
      'unknown',
      'yes',
      'MIT',
      'publisher_permissive',
      'conflicting',
    ]) {
      expect(modelLicenseStatusKey(status)).toBe('modelLicense.unknown');
    }
    expect(modelLicenseStatusKey('restricted')).toBe('modelLicense.restricted');
  });
  it('rejects changed identities, upstream-assent documents and absent evidence', () => {
    const plan = syntheticPlan();
    expect(canAcknowledgeModelPlan(plan, 'test/model', 'local', info.registry_digest)).toBe(true);
    for (const changed of [
      { ...plan, repo_id: 'different/model' },
      { ...plan, registry_digest: 'changed' },
      { ...plan, documents: [] },
      { ...plan, blockers: ['terms_unverified'] },
      { ...plan, documents: [{ ...plan.documents[0], agreement_required: true }] },
    ])
      expect(canAcknowledgeModelPlan(changed, 'test/model', 'local', info.registry_digest)).toBe(
        false,
      );
    expect(canAcknowledgeModelPlan(plan, 'test/model', 'worker:test', info.registry_digest)).toBe(
      false,
    );
  });
  it('allows only HTTPS source URLs without embedded credentials', () => {
    for (const value of [
      'javascript:alert(1)',
      'data:text/html,hi',
      'file:///tmp/a',
      'https://user:secret@example.org',
      'invalid',
    ]) {
      expect(modelLicenseUrl(value)).toBeNull();
    }
    expect(modelLicenseUrl('https://example.org/terms')).toBe('https://example.org/terms');
  });
});
