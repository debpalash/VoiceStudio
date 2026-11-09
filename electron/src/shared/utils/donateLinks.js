// Single source of truth for donation destinations. Referenced by the
// Support page (full donate view) and the footer's support-links popover,
// so a link change can never leave one surface pointing somewhere stale.
// GitHub Sponsors leads (monthly or one-time, credited on the sponsor's GitHub
// profile); Ko-fi and PayPal remain for supporters without a GitHub account.
// The supporter picks which — no default-charge nudge, no amount pre-selected.
export const GITHUB_SPONSORS_URL = 'https://github.com/sponsors/debpalash';
export const KOFI_URL = 'https://ko-fi.com/debpalash';
export const PAYPAL_URL = 'https://paypal.me/palashCoder';
