/**
 * E.164 phone numbers: `+`, a country code and at most 15 digits in total.
 * Users paste numbers with spaces, dashes, dots and brackets; those are
 * formatting only and are stripped before validation.
 */
const SEPARATORS = /[\s().\- ]/g;

/** Calling code → ISO region. The longest matching code wins. */
const CALLING_CODES: ReadonlyArray<readonly [string, string]> = [
  ['971', 'AE'],
  ['966', 'SA'],
  ['880', 'BD'],
  ['852', 'HK'],
  ['886', 'TW'],
  ['972', 'IL'],
  ['353', 'IE'],
  ['351', 'PT'],
  ['358', 'FI'],
  ['380', 'UA'],
  ['420', 'CZ'],
  ['234', 'NG'],
  ['254', 'KE'],
  ['212', 'MA'],
  ['20', 'EG'],
  ['27', 'ZA'],
  ['30', 'GR'],
  ['31', 'NL'],
  ['32', 'BE'],
  ['33', 'FR'],
  ['34', 'ES'],
  ['36', 'HU'],
  ['39', 'IT'],
  ['40', 'RO'],
  ['41', 'CH'],
  ['43', 'AT'],
  ['44', 'GB'],
  ['45', 'DK'],
  ['46', 'SE'],
  ['47', 'NO'],
  ['48', 'PL'],
  ['49', 'DE'],
  ['51', 'PE'],
  ['52', 'MX'],
  ['54', 'AR'],
  ['55', 'BR'],
  ['56', 'CL'],
  ['57', 'CO'],
  ['60', 'MY'],
  ['61', 'AU'],
  ['62', 'ID'],
  ['63', 'PH'],
  ['64', 'NZ'],
  ['65', 'SG'],
  ['66', 'TH'],
  ['81', 'JP'],
  ['82', 'KR'],
  ['84', 'VN'],
  ['86', 'CN'],
  ['90', 'TR'],
  ['91', 'IN'],
  ['92', 'PK'],
];
// +1 (US, Canada, Caribbean) and +7 (Russia, Kazakhstan) are shared by several
// countries; naming one of them would mislabel the number, so give no hint.

/** Strip formatting; a leading `00` international prefix becomes `+`. */
export function normalizePhone(input: string): string {
  const compact = input.replace(SEPARATORS, '');
  return compact.startsWith('00') ? `+${compact.slice(2)}` : compact;
}

export type PhoneProblem = 'empty' | 'missing_plus' | 'invalid' | null;

export function phoneProblem(input: string): PhoneProblem {
  const value = normalizePhone(input);
  if (!value) return 'empty';
  if (!value.startsWith('+')) return /^\d+$/.test(value) ? 'missing_plus' : 'invalid';
  return /^\+[1-9]\d{7,14}$/.test(value) ? null : 'invalid';
}

export function isE164(input: string): boolean {
  return phoneProblem(input) === null;
}

/** ISO region for the number's country code, when it is one we recognise. */
export function phoneRegion(input: string): string | null {
  const value = normalizePhone(input);
  if (!value.startsWith('+')) return null;
  const digits = value.slice(1);
  let best: readonly [string, string] | null = null;
  for (const entry of CALLING_CODES) {
    if (digits.startsWith(entry[0]) && (!best || entry[0].length > best[0].length)) best = entry;
  }
  return best ? best[1] : null;
}

/** Localized country name for the hint under the number field. */
export function phoneCountry(input: string, locale: string): string | null {
  const region = phoneRegion(input);
  if (!region) return null;
  try {
    return new Intl.DisplayNames([locale], { type: 'region' }).of(region) ?? region;
  } catch {
    return region;
  }
}
