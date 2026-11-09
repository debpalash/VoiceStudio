// Register jest-dom on the expect that `vp test` runs. The package's
// `@testing-library/jest-dom/vitest` entry extends the separate `vitest`
// package instead, which breaks `rejects.toThrow` under Vite+ 1.x.
import * as matchers from '@testing-library/jest-dom/matchers';
import { expect } from 'vite-plus/test';

expect.extend(matchers);

// Type-only: keeps jest-dom's matcher types on `vitest`'s Assertion.
import type {} from '@testing-library/jest-dom/vitest';
