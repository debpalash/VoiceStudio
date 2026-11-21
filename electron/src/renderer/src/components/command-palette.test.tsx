import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ gate: null as null | Promise<void>, fail: false }));

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }));
vi.mock('./command-palette-dialog', async () => {
  if (mocks.gate) await mocks.gate;
  return {
    CommandPaletteDialog: () => {
      // The lazy wrapper rethrows a failed chunk during render; throw there to match.
      if (mocks.fail) throw new Error('chunk failed');
      return <div role="dialog">palette</div>;
    },
  };
});

afterEach(() => {
  vi.resetModules();
  mocks.gate = null;
  mocks.fail = false;
});

async function load() {
  const { CommandPalette } = await import('./command-palette');
  return render(<CommandPalette />);
}

it('Escape while the dialog chunk is pending keeps it from opening later', async () => {
  let release!: () => void;
  mocks.gate = new Promise<void>((resolve) => (release = resolve));
  await load();
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  fireEvent.keyDown(window, { key: 'Escape' });
  await act(async () => {
    release();
    await mocks.gate;
  });
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('a failed chunk load closes the palette instead of crashing the shell', async () => {
  mocks.fail = true;
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  await load();
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(screen.queryByRole('dialog')).toBeNull();
});
