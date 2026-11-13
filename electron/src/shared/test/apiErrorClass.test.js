/**
 * #1773 — an unclassified 500 must name the failure it actually was.
 *
 * The backend's 500 handler has always put `error_class` in the response body,
 * but nothing lifted it onto the Error object. The auto bug reporter reads the
 * Error, so it filed "VoiceStudio hit an internal error; check the backend log
 * for details." and nothing else — every such report identical, none of them
 * triageable.
 *
 * #1956 fixed the streaming path the same way. This is the classic path, which
 * had been carrying the datum on the wire the whole time.
 */
import { describe, it, expect } from 'vitest';

import { ApiError } from '../api/client';

describe('ApiError carries the backend error class', () => {
  it('keeps a string class', () => {
    const err = new ApiError('500 Internal Server Error: boom', {
      status: 500,
      detail: { detail: 'boom', error_class: 'MemoryError' },
      errorClass: 'MemoryError',
    });
    expect(err.errorClass).toBe('MemoryError');
  });

  it('leaves it undefined when the backend sent none', () => {
    // Not every failure has one — a 404 or a validation error carries no class,
    // and inventing an empty string would put a blank line in every report.
    const err = new ApiError('404 Not Found: nope', { status: 404, detail: 'nope' });
    expect(err.errorClass).toBeUndefined();
  });

  it('ignores a non-string class rather than stringifying it', () => {
    const err = new ApiError('500', { status: 500, errorClass: { nope: true } });
    expect(err.errorClass).toBeUndefined();
  });
});
