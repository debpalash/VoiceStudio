import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import useResponsiveShellSize from './useResponsiveShellSize';

let activeObserver;
let originalClientWidth;

class ResizeObserverMock {
  constructor(callback) {
    this.callback = callback;
    activeObserver = this;
  }

  observe() {}

  disconnect() {}

  trigger() {
    this.callback([]);
  }
}

function Harness({ mounted, width }) {
  const { observeShell, shellSizeClass } = useResponsiveShellSize(1, 'native');
  return (
    <>
      <output data-testid="shell-class">{shellSizeClass || 'wide'}</output>
      {mounted && <div ref={observeShell} data-test-width={width} />}
    </>
  );
}

describe('useResponsiveShellSize', () => {
  beforeEach(() => {
    activeObserver = undefined;
    originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth');
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get() {
        return Number(this.dataset.testWidth || 0);
      },
    });
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    if (originalClientWidth) {
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth);
    } else {
      Reflect.deleteProperty(HTMLElement.prototype, 'clientWidth');
    }
  });

  it('measures a shell mounted after bootstrap and follows both breakpoints', () => {
    const view = render(<Harness mounted={false} width={1200} />);
    expect(screen.getByTestId('shell-class')).toHaveTextContent('wide');

    view.rerender(<Harness mounted width={1200} />);
    expect(screen.getByTestId('shell-class')).toHaveTextContent('wide');
    expect(activeObserver).toBeInstanceOf(ResizeObserverMock);

    view.rerender(<Harness mounted width={1100} />);
    act(() => activeObserver.trigger());
    expect(screen.getByTestId('shell-class')).toHaveTextContent('shell-narrow');

    view.rerender(<Harness mounted width={600} />);
    act(() => activeObserver.trigger());
    expect(screen.getByTestId('shell-class')).toHaveTextContent('shell-mini');

    view.rerender(<Harness mounted width={1101} />);
    act(() => activeObserver.trigger());
    expect(screen.getByTestId('shell-class')).toHaveTextContent('wide');
  });
});
