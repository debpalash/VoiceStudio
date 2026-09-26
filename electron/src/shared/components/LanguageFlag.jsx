import React from 'react';
import { Globe } from 'lucide-react';
import AF from 'country-flag-icons/react/3x2/AF';
import AL from 'country-flag-icons/react/3x2/AL';
import AM from 'country-flag-icons/react/3x2/AM';
import AZ from 'country-flag-icons/react/3x2/AZ';
import BA from 'country-flag-icons/react/3x2/BA';
import BD from 'country-flag-icons/react/3x2/BD';
import BG from 'country-flag-icons/react/3x2/BG';
import BY from 'country-flag-icons/react/3x2/BY';
import CN from 'country-flag-icons/react/3x2/CN';
import CZ from 'country-flag-icons/react/3x2/CZ';
import DE from 'country-flag-icons/react/3x2/DE';
import DK from 'country-flag-icons/react/3x2/DK';
import EE from 'country-flag-icons/react/3x2/EE';
import ES from 'country-flag-icons/react/3x2/ES';
import ES_CT from 'country-flag-icons/react/3x2/ES-CT';
import ET from 'country-flag-icons/react/3x2/ET';
import FI from 'country-flag-icons/react/3x2/FI';
import FR from 'country-flag-icons/react/3x2/FR';
import GB from 'country-flag-icons/react/3x2/GB';
import GB_SCT from 'country-flag-icons/react/3x2/GB-SCT';
import GB_WLS from 'country-flag-icons/react/3x2/GB-WLS';
import GE from 'country-flag-icons/react/3x2/GE';
import GR from 'country-flag-icons/react/3x2/GR';
import HR from 'country-flag-icons/react/3x2/HR';
import HT from 'country-flag-icons/react/3x2/HT';
import HU from 'country-flag-icons/react/3x2/HU';
import ID from 'country-flag-icons/react/3x2/ID';
import IL from 'country-flag-icons/react/3x2/IL';
import IN from 'country-flag-icons/react/3x2/IN';
import IQ from 'country-flag-icons/react/3x2/IQ';
import IR from 'country-flag-icons/react/3x2/IR';
import IS from 'country-flag-icons/react/3x2/IS';
import IT from 'country-flag-icons/react/3x2/IT';
import JP from 'country-flag-icons/react/3x2/JP';
import KG from 'country-flag-icons/react/3x2/KG';
import KH from 'country-flag-icons/react/3x2/KH';
import KR from 'country-flag-icons/react/3x2/KR';
import KZ from 'country-flag-icons/react/3x2/KZ';
import LA from 'country-flag-icons/react/3x2/LA';
import LK from 'country-flag-icons/react/3x2/LK';
import LT from 'country-flag-icons/react/3x2/LT';
import LV from 'country-flag-icons/react/3x2/LV';
import MK from 'country-flag-icons/react/3x2/MK';
import MM from 'country-flag-icons/react/3x2/MM';
import MN from 'country-flag-icons/react/3x2/MN';
import MT from 'country-flag-icons/react/3x2/MT';
import MY from 'country-flag-icons/react/3x2/MY';
import NG from 'country-flag-icons/react/3x2/NG';
import NL from 'country-flag-icons/react/3x2/NL';
import NO from 'country-flag-icons/react/3x2/NO';
import NP from 'country-flag-icons/react/3x2/NP';
import NZ from 'country-flag-icons/react/3x2/NZ';
import PK from 'country-flag-icons/react/3x2/PK';
import PL from 'country-flag-icons/react/3x2/PL';
import PT from 'country-flag-icons/react/3x2/PT';
import RO from 'country-flag-icons/react/3x2/RO';
import RS from 'country-flag-icons/react/3x2/RS';
import RU from 'country-flag-icons/react/3x2/RU';
import SA from 'country-flag-icons/react/3x2/SA';
import SE from 'country-flag-icons/react/3x2/SE';
import SI from 'country-flag-icons/react/3x2/SI';
import SK from 'country-flag-icons/react/3x2/SK';
import SO from 'country-flag-icons/react/3x2/SO';
import TH from 'country-flag-icons/react/3x2/TH';
import TJ from 'country-flag-icons/react/3x2/TJ';
import TR from 'country-flag-icons/react/3x2/TR';
import TW from 'country-flag-icons/react/3x2/TW';
import TZ from 'country-flag-icons/react/3x2/TZ';
import UA from 'country-flag-icons/react/3x2/UA';
import US from 'country-flag-icons/react/3x2/US';
import UZ from 'country-flag-icons/react/3x2/UZ';
import VA from 'country-flag-icons/react/3x2/VA';
import VN from 'country-flag-icons/react/3x2/VN';
import WS from 'country-flag-icons/react/3x2/WS';
import ZA from 'country-flag-icons/react/3x2/ZA';
import ZW from 'country-flag-icons/react/3x2/ZW';

// Languages do not map one-to-one to countries. These are stable,
// representative flags for quick visual scanning; regional language variants
// keep their explicit flag (Simplified Chinese → CN, Traditional → TW).
// oxlint-disable-next-line react/only-export-components -- exported for the coverage regression test
export const LANGUAGE_FLAGS = {
  af: ZA,
  sq: AL,
  am: ET,
  ar: SA,
  hy: AM,
  az: AZ,
  eu: ES,
  be: BY,
  bn: BD,
  bs: BA,
  bg: BG,
  my: MM,
  ca: ES_CT,
  'cmn-Hans': CN,
  'cmn-Hant': TW,
  hr: HR,
  cs: CZ,
  da: DK,
  nl: NL,
  en: GB,
  et: EE,
  fi: FI,
  fr: FR,
  gl: ES,
  ka: GE,
  de: DE,
  el: GR,
  gu: IN,
  ht: HT,
  ha: NG,
  haw: US,
  he: IL,
  hi: IN,
  hu: HU,
  is: IS,
  id: ID,
  it: IT,
  ja: JP,
  jw: ID,
  kn: IN,
  kk: KZ,
  km: KH,
  ko: KR,
  ku: IQ,
  ky: KG,
  lo: LA,
  la: VA,
  lv: LV,
  lt: LT,
  mk: MK,
  ms: MY,
  ml: IN,
  mt: MT,
  mi: NZ,
  mr: IN,
  mn: MN,
  ne: NP,
  no: NO,
  ps: AF,
  fa: IR,
  pl: PL,
  pt: PT,
  pa: IN,
  ro: RO,
  ru: RU,
  sm: WS,
  gd: GB_SCT,
  sr: RS,
  sn: ZW,
  sd: PK,
  si: LK,
  sk: SK,
  sl: SI,
  so: SO,
  es: ES,
  su: ID,
  sw: TZ,
  sv: SE,
  tg: TJ,
  ta: IN,
  te: IN,
  th: TH,
  tr: TR,
  uk: UA,
  ur: PK,
  uz: UZ,
  vi: VN,
  cy: GB_WLS,
  xh: ZA,
  yi: IL,
  yo: NG,
  zu: ZA,
};

export default function LanguageFlag({ code, className = '' }) {
  const Flag = LANGUAGE_FLAGS[code];
  if (!Flag) {
    return (
      <Globe
        size={16}
        aria-hidden="true"
        className={`shrink-0 text-[color:var(--chrome-fg-dim)] ${className}`}
        data-language-flag={code}
        data-testid={`language-flag-${code}`}
      />
    );
  }
  return (
    <Flag
      aria-hidden="true"
      className={`h-[12px] w-[18px] shrink-0 rounded-[2px] shadow-[0_0_0_1px_rgba(255,255,255,0.14)] ${className}`}
      data-language-flag={code}
      data-testid={`language-flag-${code}`}
    />
  );
}
