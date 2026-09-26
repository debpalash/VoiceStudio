import { fireEvent, render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { describe, expect, it, vi } from 'vitest';
import i18n from '../../i18n';
import DubPipelineStepper from './DubPipelineStepper';

describe('DubPipelineStepper navigation', () => {
  it('makes reachable stages keyboard-accessible actions', () => {
    const onStepSelect = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <DubPipelineStepper
          dubStep="idle"
          selectableSteps={['prepare', 'transcribe']}
          onStepSelect={onStepSelect}
        />
      </I18nextProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Transcribe' }));
    expect(onStepSelect).toHaveBeenCalledWith('transcribe');
    expect(screen.getByText('Edit').closest('button')).toBeNull();
  });
});
