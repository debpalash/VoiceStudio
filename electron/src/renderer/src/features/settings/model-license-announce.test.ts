import { afterEach, expect, it, vi } from 'vitest';
import {
  announceModelLicenceRequired,
  MODEL_LICENCE_REQUIRED_EVENT,
} from './model-license-contract';

const licence = {
  code: 'model_licence_required',
  message: 'Accept the licence',
  models: [{ repo_id: 'org/model', license: 'cc-by-nc-4.0', category: 'noncommercial', fingerprint: 'fp1' }],
};

afterEach(() => vi.restoreAllMocks());

it('dispatches the requirements for a model_licence_required payload', () => {
  const seen = vi.fn();
  window.addEventListener(MODEL_LICENCE_REQUIRED_EVENT, seen);
  expect(announceModelLicenceRequired(licence)).toBe(true);
  expect(announceModelLicenceRequired({ detail: licence })).toBe(true);
  window.removeEventListener(MODEL_LICENCE_REQUIRED_EVENT, seen);
  expect(seen).toHaveBeenCalledTimes(2);
  expect((seen.mock.calls[0][0] as CustomEvent).detail).toEqual(licence.models);
});
it('ignores other errors and malformed payloads', () => {
  const seen = vi.fn();
  window.addEventListener(MODEL_LICENCE_REQUIRED_EVENT, seen);
  expect(announceModelLicenceRequired({ code: 'other', models: licence.models })).toBe(false);
  expect(announceModelLicenceRequired({ code: licence.code, models: [{}] })).toBe(false);
  expect(announceModelLicenceRequired(null)).toBe(false);
  window.removeEventListener(MODEL_LICENCE_REQUIRED_EVENT, seen);
  expect(seen).not.toHaveBeenCalled();
});
it('reads a frame whose own detail is a plain string (TTS WebSocket)', () => {
  expect(announceModelLicenceRequired({ type: 'error', detail: 'text', ...licence })).toBe(true);
});
