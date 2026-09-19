import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { FilmIcon } from 'lucide-react';
import { SecondarySidebar } from './workspace-sidebar';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('allows a 40% wider spacious pane and restores the saved width on remount', () => {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1400);
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  const pane = (
    <SecondarySidebar title="Dub" icon={FilmIcon} size="spacious">
      <section>Preview</section>
    </SecondarySidebar>
  );
  const first = render(pane);
  const separator = screen.getByRole('separator');
  expect(separator).toHaveAttribute('aria-valuemax', '750');
  fireEvent.keyDown(separator, { key: 'ArrowRight' });
  expect(separator).toHaveAttribute('aria-valuenow', '436');
  expect(localStorage.getItem('voicestudio.secondary-sidebar.spacious')).toBe('436');
  first.unmount();
  render(pane);
  expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow', '436');
});

it('pins a footer outside the scrolling content and reports collapse so a page can re-home it', () => {
  const onCollapsedChange = vi.fn();
  render(
    <SecondarySidebar
      title="Stories"
      icon={FilmIcon}
      footer={<button type="button">Generate</button>}
      onCollapsedChange={onCollapsedChange}
    >
      <section>Setup</section>
    </SecondarySidebar>,
  );
  const footer = document.querySelector('[data-slot="secondary-sidebar-footer"]');
  const content = document.querySelector('[data-slot="secondary-sidebar-content"]');
  expect(footer).not.toBeNull();
  expect(content?.contains(footer)).toBe(false); // never scrolls away with the setup cards
  expect(screen.getByRole('button', { name: 'Generate' })).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: /collapse/i }));
  expect(onCollapsedChange).toHaveBeenLastCalledWith(true);
  expect(document.querySelector('[data-slot="secondary-sidebar-footer"]')).toBeNull();
});
