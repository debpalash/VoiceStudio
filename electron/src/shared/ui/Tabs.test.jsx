import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TabsContent } from '@/components/ui/tabs';
import Tabs from './Tabs';

describe('Tabs', () => {
  it('preserves Radix trigger associations when no ID prefix is supplied', () => {
    render(
      <Tabs items={[{ id: 'first', label: 'First' }]} value="first" onChange={vi.fn()}>
        <TabsContent value="first">First panel</TabsContent>
      </Tabs>,
    );

    const trigger = screen.getByRole('tab', { name: 'First' });
    const panel = screen.getByRole('tabpanel');
    expect(panel.id).toBe(trigger.getAttribute('aria-controls'));
    expect(panel).toHaveAttribute('aria-labelledby', trigger.id);
  });
});
