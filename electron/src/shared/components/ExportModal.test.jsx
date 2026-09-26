// Regression for #183: a dub job's Export modal crashed DubTab's ErrorBoundary
// with "TypeError: e is not a function" — the i18n sweep added t('…') calls
// inside `.map(t => …)` callbacks where `t` was the loop variable, shadowing the
// useTranslation `t`. Rendering with a dub track that equals the primary
// dubLangCode exercises the exact crashing branch.
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import '../i18n';
import ExportModal from './ExportModal';

const noop = () => {};

function renderModal(extra = {}) {
  return render(
    <ExportModal
      open
      onClose={noop}
      jobId="job1"
      filename="video.mp4"
      dubTracks={['es', 'fr']}
      dubLangCode="es"
      preserveBg={false}
      setPreserveBg={noop}
      defaultTrack="original"
      setDefaultTrack={noop}
      exportTracks={{}}
      setExportTracks={noop}
      dualSubs={false}
      setDualSubs={noop}
      burnSubs={false}
      setBurnSubs={noop}
      API=""
      triggerDownload={noop}
      handleDubDownload={noop}
      handleDubAudioDownload={noop}
      handleAudioExport={noop}
      segmentCount={3}
      {...extra}
    />,
  );
}

describe('ExportModal (regression #183)', () => {
  it.each(['search', 'option'])('keeps the drawer open for a portaled selector %s', (target) => {
    const onClose = vi.fn();
    const setDefaultTrack = vi.fn();
    renderModal({ onClose, setDefaultTrack });
    fireEvent.click(screen.getByRole('button', { name: /default audio track/i }));
    expect(document.querySelector('.export-drawer')).not.toContainElement(
      screen.getByRole('listbox'),
    );

    fireEvent.mouseDown(
      target === 'search'
        ? screen.getByRole('textbox', { name: /search/i })
        : screen.getByRole('option', { name: /^ES/ }),
    );
    expect(onClose).not.toHaveBeenCalled();
    if (target === 'option') expect(setDefaultTrack).toHaveBeenCalledWith('es');

    fireEvent.mouseDown(document.querySelector('.export-summary'));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('dismisses the selector before the export drawer when Escape is pressed', () => {
    const onClose = vi.fn();
    renderModal({ onClose });
    const trigger = screen.getByRole('button', { name: /default audio track/i });
    fireEvent.click(trigger);
    fireEvent.keyDown(screen.getByRole('textbox', { name: /search/i }), { key: 'Escape' });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(trigger).toHaveFocus();

    fireEvent.keyDown(trigger, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('keeps export actions outside the scrolling settings and uses themed track choices', () => {
    const setDefaultTrack = vi.fn();
    renderModal({ setDefaultTrack });
    expect(document.querySelector('.export-body')).not.toContainElement(
      document.querySelector('.export-summary'),
    );
    fireEvent.click(screen.getByRole('button', { name: /default audio track/i }));
    fireEvent.mouseDown(screen.getByRole('option', { name: /^ES/ }));
    expect(setDefaultTrack).toHaveBeenCalledWith('es');
    expect(screen.getAllByRole('switch')).toHaveLength(2);
  });
  it('renders with dub tracks incl. the primary dub without throwing', () => {
    expect(() => renderModal()).not.toThrow();
  });

  it('renders nothing when closed', () => {
    const { container } = render(
      <ExportModal open={false} onClose={noop} dubTracks={[]} exportTracks={{}} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
