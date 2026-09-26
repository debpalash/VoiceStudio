import React, { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import '../../i18n';

import ActionBar from './ActionBar';

const setter = vi.fn();
const baseProps = {
  t: (key) => key,
  cfg: 2,
  setCfg: setter,
  speed: 1,
  setSpeed: setter,
  tShift: 0.5,
  setTShift: setter,
  posTemp: 1,
  setPosTemp: setter,
  classTemp: 1,
  setClassTemp: setter,
  layerPenalty: 1,
  setLayerPenalty: setter,
  duration: '',
  setDuration: setter,
  denoise: false,
  setDenoise: setter,
  postprocess: false,
  setPostprocess: setter,
  language: 'Auto',
  setLanguage: setter,
  steps: 16,
  setSteps: setter,
  showHearDemo: false,
  outputPlaying: false,
  isGenerating: false,
  handleGenerate: setter,
  generationTime: 0,
  generationProgress: null,
  wasGeneratingRef: { current: false },
};

function Harness() {
  const [showOverrides, setShowOverrides] = useState(false);
  return (
    <ActionBar {...baseProps} showOverrides={showOverrides} setShowOverrides={setShowOverrides} />
  );
}

describe('ActionBar', () => {
  it('labels every tuning slider and exposes audio cleanup as switches', () => {
    const setDenoise = vi.fn();
    const setPostprocess = vi.fn();
    render(
      <ActionBar
        {...baseProps}
        showOverrides
        setShowOverrides={setter}
        setDenoise={setDenoise}
        setPostprocess={setPostprocess}
      />,
    );
    for (const slider of screen.getAllByRole('slider')) expect(slider).toHaveAccessibleName();
    const denoise = screen.getByRole('switch', { name: 'clone.denoise' });
    expect(denoise).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(denoise);
    expect(setDenoise).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByRole('switch', { name: 'clone.postprocess' }));
    expect(setPostprocess).toHaveBeenCalledWith(true);
  });
  it('opens the language list above the bottom bar outside its clipping ancestors', () => {
    const { container } = render(<Harness />);
    const wrapper = screen.getByRole('button', { name: 'clone.language' });
    vi.spyOn(wrapper, 'getBoundingClientRect').mockReturnValue({
      top: 650,
      bottom: 680,
      left: 20,
      right: 320,
      width: 300,
      height: 30,
    });
    fireEvent.click(wrapper);
    const list = screen.getByRole('dialog');
    expect(container).not.toContainElement(list);
    expect(list).toHaveClass('multi-lang__drop');
    expect(list.style.bottom).not.toBe('');
    expect(screen.getAllByTestId('language-flag-es').length).toBeGreaterThan(0);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Zulu' } });
    fireEvent.click(screen.getByRole('button', { name: /Zulu/ }));
    expect(setter).toHaveBeenCalledWith('Zulu');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(wrapper).toHaveFocus();
  });

  it('keeps sampling steps inside Production Overrides', () => {
    render(<Harness />);

    expect(screen.queryByRole('slider', { name: 'clone.steps' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /clone.production_overrides/ }));
    expect(screen.getByRole('slider', { name: 'clone.steps' })).toBeInTheDocument();
  });

  it('shows indeterminate progress instead of inventing a percentage from elapsed time', () => {
    render(
      <ActionBar
        {...baseProps}
        showOverrides={false}
        setShowOverrides={setter}
        isGenerating
        generationTime="10.6"
        generationProgress={null}
      />,
    );

    const progress = screen.getByRole('progressbar');
    expect(progress).not.toHaveAttribute('aria-valuenow');
    expect(progress).toHaveAttribute('data-state', 'indeterminate');
  });

  it('shows determinate progress only when the generation path reports it', () => {
    render(
      <ActionBar
        {...baseProps}
        showOverrides={false}
        setShowOverrides={setter}
        isGenerating
        generationTime="10.6"
        generationProgress={42}
      />,
    );

    const progress = screen.getByRole('progressbar');
    expect(progress).toHaveAttribute('aria-valuenow', '42');
    expect(progress.querySelector('[data-slot="progress-indicator"]')).toHaveStyle({
      width: '42%',
    });
  });
});
