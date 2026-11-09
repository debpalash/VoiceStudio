import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { CloneReadinessNotice } from './clone-readiness-notice';

const engines = vi.hoisted(() => ({ activeTts: null as Record<string, unknown> | null }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/hooks/use-engines', () => ({ useEngines: () => engines }));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}));

afterEach(cleanup);

it('points a model that designs but cannot clone to Voice design', () => {
  engines.activeTts = { supports_cloning: false, supports_voice_design: null };
  render(<CloneReadinessNotice id="r" blocker="cloning" demo={false} />);
  expect(screen.getByRole('status')).toHaveTextContent('clone.cannotCloneUseDesign');
  expect(screen.getByRole('link', { name: 'nav.design' })).toHaveAttribute('href', '/design');
  // One next step, not a second jargon link beside it.
  expect(screen.getAllByRole('link')).toHaveLength(1);
});

it('keeps the plain notice when the model can neither clone nor design', () => {
  engines.activeTts = { supports_cloning: false, supports_voice_design: false };
  render(<CloneReadinessNotice id="r" blocker="cloning" demo={false} />);
  expect(screen.getByRole('status')).toHaveTextContent('convert.cloning_required');
  expect(screen.queryByRole('link', { name: 'nav.design' })).toBeNull();
  expect(screen.getByRole('link', { name: 'engines.set_up' })).toHaveAttribute(
    'href',
    '/settings/models/$family',
  );
});
