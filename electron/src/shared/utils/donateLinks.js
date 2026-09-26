// Single source of truth for donation destinations. Referenced by the
// Support page (full donate view) and the footer's donation-moment popover,
// so a link change can never leave one surface pointing somewhere stale.
// GitHub Sponsors isn't available, so donations go through Ko-fi or PayPal
// and the supporter picks which — no default-charge nudge, none pre-selected.
export const KOFI_URL = 'https://ko-fi.com/debpalash';
export const PAYPAL_URL = 'https://paypal.me/palashCoder';
