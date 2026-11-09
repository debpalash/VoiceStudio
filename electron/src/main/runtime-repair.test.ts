// @vitest-environment node
import { expect, it } from 'vitest';
import { decideMissingRuntime } from './runtime-repair';

const base = { packaged: true, previouslyInstalled: true, repairAttempted: false };

it('repairs a packaged runtime that lost its environment', () => {
  expect(decideMissingRuntime(base)).toBe('repair');
});
it('stops after one attempt per launch', () => {
  expect(decideMissingRuntime({ ...base, repairAttempted: true })).toBe('repair_failed');
});
it('leaves a first run to the setup wizard', () => {
  expect(decideMissingRuntime({ ...base, previouslyInstalled: false })).toBe('wizard');
});
it('never auto-repairs where the runtime cannot resolve', () => {
  expect(decideMissingRuntime({ ...base, unsupported: true })).toBe('wizard');
});
it('advises setup:api for a source checkout', () => {
  expect(decideMissingRuntime({ ...base, packaged: false })).toBe('setup_api');
});
it('asks before switching an existing runtime to Windows ROCm', () => {
  expect(decideMissingRuntime({ ...base, rocmNotYetInstalled: true })).toBe('wizard');
});
