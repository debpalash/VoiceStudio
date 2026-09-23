import { describe, it, expect } from 'vitest';
import {
  cueSourceId,
  settleCueSources,
  withOriginalCueSource,
  withoutCueSource,
} from '../utils/segments';
import { useAppStore } from '../store';

// An imported caption cue is provenance, not text (#2295): its id may be
// echoed to /dub/generate only while the segment still holds that import's
// words, never because later words happen to be equal.
const source = { id: 'imp:0', text: 'Hello', cue: '<i>Hello</i>' };
const imported = (text = 'Hello') => ({
  id: '0',
  text,
  text_original: 'Hello',
  srt_source: source,
  cue_source_id: 'imp:0',
});

describe('imported cue provenance', () => {
  it('echoes the import id only while the text is the imported text', () => {
    expect(cueSourceId(imported())).toBe('imp:0');
    expect(cueSourceId(imported('Bye'))).toBeUndefined();
    expect(cueSourceId(withoutCueSource(imported()))).toBeUndefined();
    expect(cueSourceId({ id: '0', text: 'Hello', srt_source: source })).toBeUndefined();
  });

  it('never revives a cue after the text was edited away and back', () => {
    useAppStore.setState({ dubSegments: [imported()] });
    const { setDubSegments } = useAppStore.getState();
    setDubSegments((prev) => prev.map((s) => ({ ...s, text: 'Bye' })));
    setDubSegments((prev) => prev.map((s) => ({ ...s, text: 'Hello' })));
    const [segment] = useAppStore.getState().dubSegments;
    expect(segment.srt_source).toEqual(source);
    expect(cueSourceId(segment)).toBeUndefined();
  });

  it('restores the cue when a row is back on its untouched original text', () => {
    const translated = withoutCueSource({ ...imported(), text: 'Hola' });
    expect(cueSourceId(withOriginalCueSource({ ...translated, text: 'Hello' }))).toBe('imp:0');
    expect(cueSourceId(withOriginalCueSource(translated))).toBeUndefined();
  });

  it('keeps array identity when nothing is stale', () => {
    const segments = [imported()];
    expect(settleCueSources(segments)).toBe(segments);
    const plain = { id: '1', text: 'x' };
    expect(withoutCueSource(plain)).toBe(plain);
  });
});
