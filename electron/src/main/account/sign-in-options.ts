import type { AccountSignInOptions } from '../../preload/index.d';
export function parseSignInOptions(raw: unknown): AccountSignInOptions {
  if (raw === undefined) return { intent: 'login' };
  if (
    !raw ||
    typeof raw !== 'object' ||
    Array.isArray(raw) ||
    Object.keys(raw).some((key) => key !== 'intent') ||
    !(
      (raw as { intent?: unknown }).intent === 'login' ||
      (raw as { intent?: unknown }).intent === 'signup'
    )
  )
    throw new Error('Invalid sign-in options');
  return { intent: (raw as AccountSignInOptions).intent };
}
