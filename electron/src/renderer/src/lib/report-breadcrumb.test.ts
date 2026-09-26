import { beforeEach, describe, expect, it, vi } from 'vitest';
import { clearBreadcrumbs, getBreadcrumbs } from '@shared/utils/breadcrumbs';
import {
  recordActionBreadcrumb,
  recordRouteBreadcrumb,
  routeBreadcrumb,
} from './report-breadcrumb';
const capture = vi.hoisted(() => vi.fn());
const capturePageview = vi.hoisted(() => vi.fn());
vi.mock('@shared/utils/analytics', () => ({ capture, capturePageview }));

beforeEach(() => {
  clearBreadcrumbs();
  capture.mockClear();
  capturePageview.mockClear();
});

describe('routeBreadcrumb', () => {
  it('keeps only fixed workspace and settings labels', () => {
    expect(routeBreadcrumb('#/clone')).toBe('view:clone');
    expect(routeBreadcrumb('#/settings/models/tts')).toBe('view:settings/models');
    expect(routeBreadcrumb('#/personas?query=private-name')).toBe('view:personas');
    expect(routeBreadcrumb('#/private/user/content')).toBe('view:other');
    expect(routeBreadcrumb('#/integrations/private-slug')).toBe('view:integrations');
  });
});

describe('recordActionBreadcrumb', () => {
  it('records fixed workflow actions and rejects dynamic values', () => {
    recordActionBreadcrumb('generate:clone:start');
    recordActionBreadcrumb('private-file:C:/Users/name/voice.wav' as never);
    expect(getBreadcrumbs().map((entry) => entry.action)).toEqual(['generate:clone:start']);
    expect(capture).toHaveBeenCalledOnce();
    expect(capture).toHaveBeenCalledWith('workflow_action', { stage: 'generate:clone:start' });
  });
});

it('captures only the fixed screen label, never a route parameter', () => {
  recordRouteBreadcrumb('#/settings/models/my-private-model');
  expect(capture).toHaveBeenCalledWith('screen_viewed', { stage: 'view:settings/models' });
  expect(capturePageview).toHaveBeenCalledWith('view:settings/models');
});
