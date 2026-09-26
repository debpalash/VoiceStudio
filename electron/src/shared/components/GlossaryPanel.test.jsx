import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import GlossaryPanel from './GlossaryPanel';
import { listGlossary, addGlossaryTerm, updateGlossaryTerm } from '../api/glossary';

vi.mock('../api/glossary', () => ({
  listGlossary: vi.fn(),
  addGlossaryTerm: vi.fn(),
  updateGlossaryTerm: vi.fn(),
  deleteGlossaryTerm: vi.fn(),
  clearGlossary: vi.fn(),
  autoExtractGlossary: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  listGlossary.mockResolvedValue([]);
});

describe('Glossary term entry', () => {
  it('offers labelled fields without an empty table and submits from the keyboard', async () => {
    const onChange = vi.fn();
    addGlossaryTerm.mockResolvedValue({ id: 1, source: 'Studio', target: 'Estudio', note: '' });
    render(<GlossaryPanel projectId="test" targetLang="es" onChange={onChange} />);
    await screen.findByText('No terms yet. Add one below or click Auto.');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add term' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Source'), { target: { value: 'Studio' } });
    fireEvent.change(screen.getByLabelText('Target'), { target: { value: 'Estudio' } });
    fireEvent.submit(screen.getByLabelText('Target').closest('form'));
    await waitFor(() =>
      expect(addGlossaryTerm).toHaveBeenCalledWith('test', {
        source: 'Studio',
        target: 'Estudio',
        note: '',
      }),
    );
    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(screen.getByLabelText('Source')).toHaveValue('');
  });

  it('makes editing an existing term available through a named button', async () => {
    listGlossary.mockResolvedValue([{ id: 1, source: 'Studio', target: 'Estudio', note: '' }]);
    updateGlossaryTerm.mockResolvedValue({
      id: 1,
      source: 'Studio',
      target: 'Estudio nuevo',
      note: '',
    });
    render(<GlossaryPanel projectId="test" targetLang="es" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const table = screen.getByRole('table');
    const target = table.querySelector('input[aria-label="Target"]');
    fireEvent.change(target, { target: { value: 'Estudio nuevo' } });
    fireEvent.keyDown(target, { key: 'Enter' });
    await waitFor(() =>
      expect(updateGlossaryTerm).toHaveBeenCalledWith('test', 1, {
        source: 'Studio',
        target: 'Estudio nuevo',
        note: '',
      }),
    );
  });
});
