import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vitest';
import {
  modelLicenseCategory,
  modelLicenseCategoryKey,
  modelLicenseStatus,
} from './model-license-contract';
import { ModelLicenseIcon } from './model-license-icon';

describe('model licence categories and icons', () => {
  it('maps restricted and noncommercial review statuses to the restricted label', () => {
    expect(modelLicenseStatus('restricted')).toBe('restricted');
    expect(modelLicenseStatus('noncommercial_current_upstream_terms')).toBe('restricted');
    expect(modelLicenseStatus('separate_permission_required')).toBe('permission');
    for (const value of [undefined, '', 'unknown', 'permitted', 'allowed', 'future_status']) {
      expect(modelLicenseStatus(value)).toBe('unknown');
    }
  });

  it('collapses missing and future categories to unknown, never to commercial', () => {
    for (const value of ['commercial', 'conditions', 'noncommercial'] as const) {
      expect(modelLicenseCategory(value)).toBe(value);
    }
    for (const value of [undefined, '', 'unknown', 'permitted', 'Commercial', 'future']) {
      expect(modelLicenseCategory(value)).toBe('unknown');
    }
    expect(modelLicenseCategoryKey('noncommercial')).toBe('modelLicense.categoryNoncommercial');
    expect(modelLicenseCategoryKey(undefined)).toBe('modelLicense.categoryUnknown');
  });

  it('renders a decorative, distinct, toned glyph per category', () => {
    const shapes = new Set<string>();
    const tones = new Set<string>();
    for (const category of ['commercial', 'conditions', 'noncommercial', 'unknown'] as const) {
      const host = document.createElement('div');
      const root = createRoot(host);
      act(() => root.render(<ModelLicenseIcon category={category} />));
      const svg = host.querySelector('svg')!;
      expect(svg.getAttribute('aria-hidden')).toBe('true');
      expect(svg.getAttribute('data-license-category')).toBe(category);
      shapes.add(svg.innerHTML);
      tones.add(svg.getAttribute('class')!);
      act(() => root.unmount());
    }
    expect(shapes.size).toBe(4);
    expect(tones.size).toBe(4);
  });
});
