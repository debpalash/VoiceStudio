import { render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ENGINE_SELECTED_EVENT } from '@/lib/api/client';

const prime = vi.fn(async () => {});
vi.mock('@/lib/api/generate-budget', () => ({ primeGenerateBudget: () => prime() }));
vi.mock('./use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));

import { GenerateBudgetSync } from './use-generate-budget-sync';

afterEach(() => prime.mockClear());

describe('GenerateBudgetSync', () => {
  it('re-reads the budget when the backend is reachable and when the engine changes', () => {
    render(<GenerateBudgetSync />);
    expect(prime).toHaveBeenCalledTimes(1);
    window.dispatchEvent(new CustomEvent(ENGINE_SELECTED_EVENT));
    expect(prime).toHaveBeenCalledTimes(2);
  });
});
