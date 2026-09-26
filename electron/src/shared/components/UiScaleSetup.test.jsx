import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';
import UiScaleSetup from './UiScaleSetup';
import { suggestUiScale } from '../utils/uiScaleSuggestion';

/** jsdom's innerWidth/innerHeight are only redefinable, not assignable. */
const setWindowSize = (width, height) => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
};

/** Resize the window AND fire the event the component subscribes to. */
const resizeTo = (width, height) => {
  setWindowSize(width, height);
  act(() => {
    fireEvent(window, new Event('resize'));
  });
};

const renderSetup = () => {
  const setUiScale = vi.fn();
  const setUiScaleConfigured = vi.fn();
  const setUiScalePreviewed = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <UiScaleSetup
        uiScale={1}
        setUiScale={setUiScale}
        setUiScaleConfigured={setUiScaleConfigured}
        setUiScalePreviewed={setUiScalePreviewed}
      />
    </I18nextProvider>,
  );
  return { setUiScale, setUiScaleConfigured, setUiScalePreviewed };
};

describe('<UiScaleSetup />', () => {
  it('preselects the resolution suggestion and persists the checked choice', () => {
    setWindowSize(1280, 720);
    const { setUiScale, setUiScaleConfigured, setUiScalePreviewed } = renderSetup();

    expect(screen.getByTestId('ui-scale-option-80')).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByTestId('ui-scale-option-110'));
    expect(setUiScale).toHaveBeenLastCalledWith(1.1);
    expect(setUiScalePreviewed).toHaveBeenLastCalledWith(true);

    fireEvent.click(screen.getByTestId('ui-scale-setup-continue'));
    expect(setUiScaleConfigured).toHaveBeenCalledWith(true);
    expect(setUiScalePreviewed).toHaveBeenLastCalledWith(false);
    expect(setUiScale).toHaveBeenLastCalledWith(1.1);
  });

  it('supports roving keyboard selection across the scale choices', () => {
    setWindowSize(1440, 900);
    const { setUiScale } = renderSetup();
    const selected = screen.getByTestId('ui-scale-option-100');
    selected.focus();

    fireEvent.keyDown(selected, { key: 'ArrowRight' });
    expect(screen.getByTestId('ui-scale-option-110')).toHaveFocus();
    expect(screen.getByTestId('ui-scale-option-110')).toHaveAttribute('aria-checked', 'true');
    expect(setUiScale).toHaveBeenLastCalledWith(1.1);
  });

  // ── Idle-stutter regression ──────────────────────────────────────────────
  // Under Tauri the scale is applied as native webview zoom, which by design
  // keeps the CSS viewport equal to the visible window (utils/uiScaleEngine.js)
  // — so applying a scale CHANGES `window.innerWidth`. While the suggestion
  // was derived from a live viewport, that closed a loop:
  //
  //   suggest → setUiScale → setZoom → viewport changes → resize →
  //   setViewport → new suggestion → setUiScale → …
  //
  // `suggestUiScale` snaps to a discrete option, so it never settled: a
  // 1280×800 window oscillated 0.9 ↔ 1.0 indefinitely and the first-run screen
  // visibly stuttered while completely idle.
  it('does not re-suggest when its own zoom changes the viewport', () => {
    // Pin the alternation the loop rode on, so this fails loudly if the
    // suggestion mapping ever stops flip-flopping between these two sizes.
    expect(suggestUiScale({ width: 1280, height: 800 })).toBe(0.9);
    expect(suggestUiScale({ width: 1422, height: 889 })).toBe(1);

    setWindowSize(1280, 800);
    const { setUiScale } = renderSetup();

    expect(setUiScale).toHaveBeenCalledTimes(1);
    expect(setUiScale).toHaveBeenLastCalledWith(0.9);

    // Native zoom of 0.9 makes the viewport report 1280/0.9 × 800/0.9; pre-fix
    // that re-derived the suggestion as 1.0 and re-applied it, which restored
    // the original viewport and closed the loop.
    resizeTo(1422, 889);
    resizeTo(1280, 800);
    resizeTo(1422, 889);

    expect(setUiScale).toHaveBeenCalledTimes(1);
  });

  it('keeps the screen readout live even though the suggestion is latched', () => {
    setWindowSize(1280, 800);
    renderSetup();

    resizeTo(1920, 1080);

    expect(screen.getByText(/1920/)).toBeInTheDocument();
    expect(screen.getByText(/1080/)).toBeInTheDocument();
  });

  it('never overrides an explicit pick when the viewport changes', () => {
    setWindowSize(1280, 800);
    const { setUiScale } = renderSetup();

    fireEvent.click(screen.getByTestId('ui-scale-option-120'));
    setUiScale.mockClear();

    resizeTo(1920, 1080);

    expect(setUiScale).not.toHaveBeenCalled();
    expect(screen.getByTestId('ui-scale-option-120')).toHaveAttribute('aria-checked', 'true');
  });
});
