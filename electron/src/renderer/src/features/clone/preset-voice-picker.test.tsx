import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PresetVoicePicker } from './preset-voice-picker';
import { cloneSettingsStore, DEFAULT_CLONE_SETTINGS } from '@/lib/store/clone-settings';

const engine = vi.hoisted(() => ({
  active: null as { preset_voices?: string[]; default_voice?: string | null } | null,
}));
vi.mock('@/hooks/use-engines', () => ({ useEngines: () => ({ activeTts: engine.active }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

beforeEach(() => {
  cloneSettingsStore.setState(() => ({ ...DEFAULT_CLONE_SETTINGS }));
  // jsdom has no scrollIntoView; SearchableSelect scrolls the highlighted row.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

it('lists the engine presets and stores the pick (#2938)', () => {
  engine.active = { preset_voices: ['M1', 'F3'], default_voice: 'M1' };
  render(<PresetVoicePicker />);
  // AGENTS.md: shared select control, not a native <select>.
  expect(document.querySelector('select')).toBeNull();
  const trigger = screen.getByRole('button', { name: 'presetVoice.label' });
  expect(trigger).toBeInTheDocument();
  expect(trigger).toHaveTextContent('M1');
  fireEvent.click(trigger);
  expect(screen.getByRole('listbox')).toBeInTheDocument();
  const options = screen.getAllByRole('option');
  expect(options.map((o) => o.textContent?.trim())).toEqual(['M1', 'F3']);
  fireEvent.mouseDown(options[1]);
  expect(cloneSettingsStore.state.presetVoice).toBe('F3');
});

it('renders nothing for engines without presets', () => {
  engine.active = { preset_voices: [] };
  const { container } = render(<PresetVoicePicker />);
  expect(container).toBeEmptyDOMElement();
});
