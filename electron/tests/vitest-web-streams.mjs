// Vitest setup for the renderer tests that run on the `vmForks` pool.
//
// Chromium exposes the WHATWG stream classes; jsdom does not. In `vmForks` each test file runs
// inside jsdom's own context, which Vitest seeds with Node's `fetch`/`Response` but not the
// stream classes those responses are built from, so supply Node's (the same realm as
// `Response`). The `forks` pool already copied them from Node's globals.
import * as webStreams from 'node:stream/web';

for (const name of [
  'ReadableStream',
  'ReadableStreamDefaultReader',
  'WritableStream',
  'TransformStream',
  'TextEncoderStream',
  'TextDecoderStream',
  'CompressionStream',
  'DecompressionStream',
]) {
  if (typeof globalThis[name] === 'undefined') {
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value: webStreams[name],
    });
  }
}
