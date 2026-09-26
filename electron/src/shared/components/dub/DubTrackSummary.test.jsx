import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import i18n from '../../i18n';
import DubTrackSummary from './DubTrackSummary';
import CheckpointBanner from '../CheckpointBanner';

describe('dubbing review controls', () => {
  it('keeps track selection with the generated-track summary', () => {
    render(
      <DubTrackSummary
        t={i18n.t.bind(i18n)}
        dubStep="done"
        dubTracks={['bn']}
        exportTracks={{}}
        setExportTracks={vi.fn()}
        dubLangCode="bn"
      />,
    );
    expect(screen.getByTestId('dub-translation-tracks')).toHaveTextContent(/bn/i);
    expect(screen.getByTestId('dub-translation-tracks').querySelector('button')).not.toBeNull();
  });
  it('uses singular timing review wording for one segment', () => {
    render(<CheckpointBanner stage="done" count={1} timingWarnings={1} />);
    expect(screen.getByText('1 segment needs timing review')).toBeInTheDocument();
  });
  it('separates generation completion from outstanding timing review', () => {
    render(<CheckpointBanner stage="done" count={353} timingWarnings={12} />);
    expect(screen.getByText('12 segments need timing review')).toBeInTheDocument();
  });
});
