// Vitest setup for the jsdom suites (renderer and shared).
//
// Testing Library gives findBy*/waitFor 1 s by default, far below the per-test limit the
// configs already raise for shared release runners (Intel macOS, Windows ARM). There, a
// first render that loads a lazily imported screen alone can take over a second, so a
// passing test failed as "Unable to find an element". Waits get the same slow-runner
// allowance, kept below the test limit so a real miss still names the missing element.
import { configure } from '@testing-library/react';

configure({ asyncUtilTimeout: process.env.CI ? 20_000 : 4_000 });
