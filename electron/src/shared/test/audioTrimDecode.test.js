import { afterEach, describe, expect, it, vi } from 'vitest';
import { decodeToMonoLowRate } from '../utils/audioTrim.js';

// #2558: decoding must not depend on a media element's metadata event, which a
// source may never emit; the Web Audio decoder is the only authority.
describe('decodeToMonoLowRate', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubDecoder(decode) {
    const contexts = [];
    class FakeOffline {
      constructor(channels, length, sampleRate) {
        contexts.push({ channels, length, sampleRate });
      }
      decodeAudioData(arr) {
        return decode(arr);
      }
    }
    vi.stubGlobal('OfflineAudioContext', FakeOffline);
    // A media element that never reports metadata or an error.
    vi.stubGlobal(
      'Audio',
      class {
        addEventListener() {}
        set src(_v) {}
      },
    );
    return contexts;
  }

  it('decodes without waiting for media metadata', async () => {
    const decoded = { duration: 3, sampleRate: 16000 };
    const contexts = stubDecoder(async (arr) => {
      expect(arr.byteLength).toBe(4);
      return decoded;
    });
    await expect(decodeToMonoLowRate(new Blob([new Uint8Array(4)]), 16000)).resolves.toBe(decoded);
    expect(contexts).toEqual([{ channels: 1, length: 1, sampleRate: 16000 }]);
  });

  it('propagates a decoder failure', async () => {
    stubDecoder(async () => {
      throw new Error('EncodingError');
    });
    await expect(decodeToMonoLowRate(new Blob([new Uint8Array(4)]))).rejects.toThrow(
      'EncodingError',
    );
  });
});
