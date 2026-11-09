import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import '@/i18n';
import { GeneratePanel, type GenerateSession } from './generate-panel';

vi.mock('@/components/longform-activity-card', () => ({
  LongformActivityCard: () => <aside>activity-card-stub</aside>,
}));

afterEach(cleanup);

const quiet: GenerateSession = {
  active: null,
  stage: 'rendering',
  completed: 0,
  total: 0,
  failed: 0,
  stopped: false,
  chapters: [],
};
const handlers = () => ({ onGenerate: vi.fn(), onStop: vi.fn() });
const statusNode = () => document.getElementById('generate-status');

it('keeps the button locked and attaches the reason to it', () => {
  render(<GeneratePanel mode="stories" session={quiet} blocker="voice" {...handlers()} />);
  const go = screen.getByRole('button', { name: /generate/i });
  expect(go).toBeDisabled();
  expect(go.getAttribute('aria-describedby')).toBe('generate-status');
  expect(statusNode()?.textContent).toMatch(/voice/i);
});

it('runs the callback once, then offers Stop together with the tracker', () => {
  const { onGenerate, onStop } = handlers();
  const view = render(
    <GeneratePanel
      mode="audiobook"
      session={quiet}
      blocker={null}
      onGenerate={onGenerate}
      onStop={onStop}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: /create audiobook/i }));
  expect(onGenerate).toHaveBeenCalledOnce();

  const running: GenerateSession = {
    ...quiet,
    active: 'audiobook',
    completed: 0,
    total: 2,
    chapters: [
      { title: 'Opening movement', status: 'rendering' },
      { title: 'Closing movement', status: 'pending' },
    ],
  };
  view.rerender(
    <GeneratePanel
      mode="audiobook"
      session={running}
      blocker={null}
      onGenerate={onGenerate}
      onStop={onStop}
    />,
  );
  expect(screen.queryByRole('button', { name: /create audiobook/i })).toBeNull();
  const panel = within(document.querySelector<HTMLElement>('[data-slot="generate-panel"]')!);
  expect(panel.getByText('Opening movement')).toBeTruthy();
  expect(panel.getByText('Closing movement')).toBeTruthy();
  fireEvent.click(panel.getByRole('button', { name: 'Stop' }));
  expect(onStop).toHaveBeenCalledOnce();
});

it('refuses a second render while the other mode is working', () => {
  render(
    <GeneratePanel
      mode="audiobook"
      blocker="busy"
      session={{ ...quiet, active: 'stories', total: 4 }}
      {...handlers()}
    />,
  );
  expect(screen.getByText('activity-card-stub')).toBeTruthy();
  expect(
    (screen.getByRole('button', { name: /create audiobook/i }) as HTMLButtonElement).disabled,
  ).toBe(true);
  expect(statusNode()?.textContent).toMatch(/already rendering/i);
});

it('hides the engine explanation visually yet keeps it announced and linked', () => {
  render(<GeneratePanel mode="stories" session={quiet} blocker="engine" {...handlers()} />);
  expect(statusNode()?.className).toContain('sr-only');
  expect(statusNode()?.textContent?.length).toBeGreaterThan(5);
  expect(screen.getByRole('button', { name: /generate/i }).getAttribute('aria-describedby')).toBe(
    'generate-status',
  );
});

it('shows progress wording while rendering and a stopped note afterwards', () => {
  const view = render(
    <GeneratePanel
      mode="stories"
      blocker={null}
      session={{ ...quiet, active: 'stories', stage: 'starting', total: 3 }}
      {...handlers()}
    />,
  );
  expect(statusNode()?.textContent).toBe('Loading…');
  view.rerender(
    <GeneratePanel
      mode="stories"
      blocker={null}
      session={{ ...quiet, failed: 1, stopped: true }}
      {...handlers()}
    />,
  );
  expect(screen.getByText(/stopped/i)).toBeTruthy();
});

it('lets the panel and its tracker shrink so Stop stays inside a short pane', () => {
  render(
    <GeneratePanel
      mode="stories"
      blocker={null}
      session={{
        ...quiet,
        active: 'stories',
        total: 1,
        chapters: [{ title: 'One', status: 'rendering' }],
      }}
      {...handlers()}
    />,
  );
  const panel = document.querySelector('[data-slot="generate-panel"]');
  expect(panel).toHaveClass('min-h-0');
  expect(panel?.querySelector('.overflow-y-auto')).toHaveClass('min-h-0');
});
