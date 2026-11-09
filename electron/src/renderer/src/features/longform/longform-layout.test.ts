import { expect, it } from 'vitest';
import source from './longform-page.tsx?raw';

// At 1024x700 the script column was a shrinkable flex item (`min-h-0 flex-1`):
// the editor kept its min-height by overflowing its box, so the script ran
// under the "Before you create" notes and the stats line sat on the chapter
// list. The column now grows but never shrinks below its content, and the pane
// scrolls instead.
it('lets the script column grow without shrinking below its content', () => {
  expect(source).toContain('max-w-6xl flex-[1_0_auto] flex-col');
  expect(source).not.toMatch(/max-w-6xl min-h-0 flex-1/);
});
