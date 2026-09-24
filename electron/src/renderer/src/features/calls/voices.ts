import type { Profile } from '@/lib/api/types';

/**
 * Outbound calls speak as the user. Only a clone the user verified as their
 * own voice, or a designed voice that imitates nobody, may place a call.
 */
export function canCallWith(profile: Profile): boolean {
  if (profile.kind === 'design') return true;
  return profile.kind === 'clone' && Boolean(profile.verified_own_voice);
}

export function callVoices(profiles: readonly Profile[] | undefined): Profile[] {
  return (profiles ?? []).filter(canCallWith);
}
