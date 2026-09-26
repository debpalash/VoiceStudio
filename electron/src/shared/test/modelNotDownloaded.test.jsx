import { describe, expect, it } from 'vitest';
import { modelNotDownloadedPayload } from '../utils/modelNotDownloaded';

describe('modelNotDownloadedPayload', () => {
  it('owns only the typed self-service 409', () => {
    const payload = { error: 'model_not_downloaded', target_label: 'gpu2' };
    expect(modelNotDownloadedPayload({ status: 409, detail: payload })).toBe(payload);
    expect(modelNotDownloadedPayload({ status: 500, detail: 'boom' })).toBeNull();
  });
});
