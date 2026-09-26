import { useLayoutEffect, useRef } from 'react';
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ModeLifecycleBoundary from './ModeLifecycleBoundary';

function ImperativeWorkspace({ name }) {
  const hostRef = useRef(null);

  useLayoutEffect(() => {
    const host = hostRef.current;
    const owned = document.createElement('div');
    owned.dataset.owner = name;
    host.appendChild(owned);
    return () => {
      // Model a renderer whose asynchronous destroy completes after React has
      // committed the next route. If the DOM host were reused, this would
      // erase that route's children and recreate the reported ownership race.
      setTimeout(() => host.replaceChildren(), 0);
    };
  }, [name]);

  return <section ref={hostRef}>{name}</section>;
}

describe('ModeLifecycleBoundary', () => {
  it('replaces the DOM owner throughout the reported rapid navigation loop', () => {
    vi.useFakeTimers();
    const sequence = ['launchpad', 'dub', 'dub', 'launchpad', 'dub'];
    const view = render(
      <ModeLifecycleBoundary mode={sequence[0]}>
        <ImperativeWorkspace name={sequence[0]} />
      </ModeLifecycleBoundary>,
    );
    let previousHost = view.container.firstElementChild;
    let previousMode = sequence[0];

    for (const mode of sequence.slice(1)) {
      view.rerender(
        <ModeLifecycleBoundary mode={mode}>
          <ImperativeWorkspace name={mode} />
        </ModeLifecycleBoundary>,
      );
      const nextHost = view.container.firstElementChild;
      if (mode !== previousMode) {
        expect(nextHost).not.toBe(previousHost);
        expect(previousHost.isConnected).toBe(false);
      } else {
        expect(nextHost).toBe(previousHost);
      }
      expect(nextHost.querySelector('[data-owner]')?.dataset.owner).toBe(mode);
      vi.runAllTimers();
      expect(nextHost.textContent).toContain(mode);
      expect(nextHost.querySelector('[data-owner]')?.dataset.owner).toBe(mode);
      previousHost = nextHost;
      previousMode = mode;
    }
    vi.useRealTimers();
  });
});
