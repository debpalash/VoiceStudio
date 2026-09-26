import React from 'react';
import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';
import { useAppStore } from '../store';
import UiScaleControl from './UiScaleControl';

describe('<UiScaleControl />', () => {
  beforeEach(() =>
    useAppStore.setState({ uiScale: 1, uiScaleConfigured: false, uiScalePreviewed: false }),
  );

  it('adjusts the persisted scale in small readable steps', () => {
    render(
      <I18nextProvider i18n={i18n}>
        <UiScaleControl />
      </I18nextProvider>,
    );

    fireEvent.click(screen.getByTestId('ui-scale-increase'));
    expect(useAppStore.getState().uiScale).toBe(1.05);
    expect(useAppStore.getState().uiScalePreviewed).toBe(true);
    expect(screen.getByText('105%')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('ui-scale-decrease'));
    expect(useAppStore.getState().uiScale).toBe(1);
  });
});
