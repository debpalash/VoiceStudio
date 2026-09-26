/**
 * Sidebar → Projects rail: inline rename (#1952 regression).
 *
 * The Dub landing used to carry its own projects panel, and that panel was the
 * only caller of the rename endpoint. When the landing became history-only,
 * the rename affordance disappeared from the entire UI while the backend route
 * and the project list both stayed — a silently lost capability, and the reason
 * `renameProject` in App.jsx became an unused variable that failed CI lint.
 *
 * These tests pin the affordance to where projects actually live now.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const mocks = vi.hoisted(() => ({
  state: {
    mode: 'dub',
    defineMethod: 'audio',
    isSidebarCollapsed: false,
    dubStep: 'idle',
    activeProjectId: null,
  },
}));

vi.mock('../store', () => ({
  useAppStore: Object.assign((selector) => selector(mocks.state), {
    getState: () => mocks.state,
    setState: vi.fn(),
  }),
}));
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../api/client', () => ({ API: 'http://localhost:3900' }));
vi.mock('../api/dub', () => ({ clearDubHistory: vi.fn() }));
vi.mock('../api/generate', () => ({ clearHistory: vi.fn() }));
vi.mock('../utils/dialog', () => ({ askConfirm: vi.fn().mockResolvedValue(true) }));
vi.mock('./WaveformPlayer', () => ({ default: () => null }));

import Sidebar from './Sidebar';

const PROJECT = {
  id: 'p1',
  name: 'Documentary voice-over',
  updated_at: '2026-09-08T12:00:00Z',
  duration: 128,
  video_path: 'documentary.mp4',
};

function renderSidebar(overrides = {}) {
  const props = {
    sidebarTab: 'projects',
    setSidebarTab: vi.fn(),
    studioProjects: [PROJECT],
    profiles: [],
    history: [],
    dubHistory: [],
    exportHistory: [],
    saveProject: vi.fn(),
    loadProject: vi.fn(),
    deleteProject: vi.fn(),
    renameProject: vi.fn(),
    deleteHistory: vi.fn(),
    loadHistory: vi.fn(),
    loadDubHistory: vi.fn(),
    ...overrides,
  };
  render(<Sidebar {...props} />);
  return props;
}

describe('Sidebar projects rail — inline rename', () => {
  beforeEach(() => {
    mocks.state.mode = 'dub';
    mocks.state.activeProjectId = null;
  });

  it('renames a project without opening it', () => {
    const props = renderSidebar();
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));

    const input = screen.getByRole('textbox', { name: 'Rename' });
    expect(input).toHaveValue('Documentary voice-over');
    fireEvent.change(input, { target: { value: '  Documentary — final cut  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(props.renameProject).toHaveBeenCalledWith('p1', 'Documentary — final cut');
    // Clicking inside the row must not also open the project.
    expect(props.loadProject).not.toHaveBeenCalled();
  });

  it('commits on Enter and abandons on Escape', () => {
    const props = renderSidebar();
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Rename' }), {
      target: { value: 'Renamed by keyboard' },
    });
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Rename' }), { key: 'Enter' });
    expect(props.renameProject).toHaveBeenCalledWith('p1', 'Renamed by keyboard');

    props.renameProject.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Rename' }), {
      target: { value: 'Discarded' },
    });
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Rename' }), { key: 'Escape' });
    expect(props.renameProject).not.toHaveBeenCalled();
    expect(screen.getByText('Documentary voice-over')).toBeVisible();
  });

  it('ignores an empty or unchanged name', () => {
    const props = renderSidebar();
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Rename' }), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(props.renameProject).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Rename' }), { key: 'Enter' });
    expect(props.renameProject).not.toHaveBeenCalled();
  });

  it('hides the affordance when no rename handler is wired', () => {
    renderSidebar({ renameProject: undefined });
    expect(screen.queryByRole('button', { name: 'Rename' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Open/ })).toBeInTheDocument();
  });
});
