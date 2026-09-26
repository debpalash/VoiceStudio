import { describe, it, expect } from 'vitest';
import { firstSoundRequest } from '../utils/firstSound';

describe('onboarding first-sound request', () => {
  it('posts localized text and a nonempty voice-design instruction as multipart form data', () => {
    const request = firstSoundRequest('Welcome to VoiceStudio');
    expect(request.method).toBe('POST');
    expect(request.body).toBeInstanceOf(FormData);
    expect(Object.fromEntries(request.body)).toEqual({
      text: 'Welcome to VoiceStudio',
      instruct: 'middle-aged, low pitch',
      num_step: '16',
    });
  });
});
