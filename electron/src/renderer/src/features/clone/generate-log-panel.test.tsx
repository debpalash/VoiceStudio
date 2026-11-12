import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import '@/i18n';
import { GenerateLogPanel } from './generate-log-panel';

const apiJson = vi.fn();
vi.mock('@/lib/api/client', () => ({
  apiJson: (...args: unknown[]) => apiJson(...args),
}));

describe('GenerateLogPanel', () => {
  beforeEach(() => {
    localStorage.clear();
    apiJson.mockReset();
    apiJson.mockResolvedValue({ lines: ['INFO synthesizing take\n'] });
  });

  it('stays collapsed until the user enables logs', () => {
    render(<GenerateLogPanel active />);
    expect(screen.queryByLabelText('Generation logs')).not.toBeInTheDocument();
    expect(screen.getByText(/Turn on to watch/)).toBeInTheDocument();
  });

  it('polls backend logs after the toggle is enabled', async () => {
    render(<GenerateLogPanel active />);
    fireEvent.click(screen.getByRole('button', { name: /Generation logs/i }));
    await waitFor(() => expect(apiJson).toHaveBeenCalledWith('/system/logs?tail=60'));
    expect(await screen.findByText(/INFO synthesizing take/)).toBeInTheDocument();
    expect(localStorage.getItem('voicestudio.generate.showLogs')).toBe('1');
  });

  it('hides entirely when generation is idle', () => {
    const { container } = render(<GenerateLogPanel active={false} />);
    expect(container).toBeEmptyDOMElement();
  });
});
