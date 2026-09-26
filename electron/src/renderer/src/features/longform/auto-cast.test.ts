import { expect, it } from 'vitest';
import { buildAutoCast } from '@shared/utils/autoCast';
it('preserves assigned character voices and appends only new speakers', () => {
  const cast = [{ id: 'existing', name: 'Mara', profileId: 'actor' }];
  const result = buildAutoCast('[Mara] Hello.\n[Guest] Welcome.', cast, [{ id: 'narrator' }]);
  expect(result.cast).toHaveLength(2);
  expect(result.cast[0]).toMatchObject({ id: 'existing', profileId: 'actor' });
  expect(result.tracks[0]).toEqual({ character: 'existing', text: 'Hello.' });
  expect(cast).toHaveLength(1);
});
it('does not merge distinct speaker names with the same slug', () => {
  const result = buildAutoCast('[A B] First.\n[A-B] Second.', [], []);
  expect(result.cast).toHaveLength(2);
  expect(new Set(result.cast.map((c) => c.id)).size).toBe(2);
  expect(result.tracks[0].character).not.toBe(result.tracks[1].character);
});
