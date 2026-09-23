import { describe, it, expect } from 'vitest';
import { cueSourceId, settleCueSources, withoutCueSource } from '../utils/segments';
import { useAppStore } from '../store';

// An imported caption cue is provenance, not text (#2295): it may be echoed
// to /dub/generate only while the segment still holds that import's words.
const imported = (text = 'Hello') => ({
  id: '0',
  text,
  srt_source: { id: 'imp:0', text: 'Hello', cue: '<i>Hello</i>' },
});

describe('imported cue provenance', () => {
  it('echoes the import id only while the text is the imported text', () => {
    expect(cueSourceId(imported())).toBe('imp:0');
    expect(cueSourceId(imported('Bye'))).toBeUndefined();
    expect(cueSourceId({ id: '0', text: 'Hello' })).toBeUndefined();
  });

  it('never revives a cue after the text was edited away and back', () => {
    useAppStore.setState({ dubSegments: [imported()] });
    const { setDubSegments } = useAppStore.getState();
    setDubSegments((prev) => prev.map((s) => ({ ...s, text: 'Bye' })));
    setDubSegments((prev) => prev.map((s) => ({ ...s, text: 'Hello' })));
    const [segment] = useAppStore.getState().dubSegments;
    expect(segment.srt_source).toBeUndefined();
    expect(cueSourceId(segment)).toBeUndefined();
  });

  it('keeps array identity when nothing is stale', () => {
    const segments = [imported()];
    expect(settleCueSources(segments)).toBe(segments);
    const plain = { id: '1', text: 'x' };
    expect(withoutCueSource(plain)).toBe(plain);
  });
});
