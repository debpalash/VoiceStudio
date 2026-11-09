// Single source of truth for VoiceStudio's sponsors + the "become a sponsor"
// contact links. Consumed by the Support page's Sponsors section and the
// footer's Sponsors link, so a logo or link change lands in exactly one place.
//
// Local-first / zero-token by design: "Become a sponsor" opens a PREFILLED
// GitHub issue (same pattern as the bug reporter, utils/bugReport.js) — the
// user reviews and submits it in their own browser; VoiceStudio never POSTs or
// holds a credential.

import { GITHUB_SPONSORS_URL, KOFI_URL } from '../utils/donateLinks';
import supermetryMark from '../assets/sponsors/supermetry-mark.svg';
import opengtmMark from '../assets/sponsors/opengtm-mark.svg';
import opalMark from '../assets/sponsors/opal-mark.svg';

const REPO = 'https://github.com/debpalash/VoiceStudio';

/**
 * Active sponsors. The Support page renders a "be the first" placeholder while
 * this is empty.
 *
 * To add a sponsor, append an entry here AND to SPONSORS.md (repo root) — the
 * two are kept in lockstep (in-app grid ⇄ README / project-site credit), which
 * tests/test_sponsors_lockstep.py enforces:
 *
 *   {
 *     name:    'Acme Corp',                // display name + <img alt>
 *     logoUrl: acmeLogo,                   // a BUNDLED import (CSP img-src 'self'); never a remote URL
 *     url:     'https://acme.example/?…',  // where the logo links to (utm_content=in-app-support)
 *     tier:    'strategic',                // one of SPONSOR_TIERS
 *   }
 *
 * @type {{ name: string, logoUrl: string, url: string, tier: string }[]}
 */
export const SPONSORS = [
  {
    name: 'OpenGTM',
    logoUrl: opengtmMark,
    url: 'https://opengtm.palash.dev/?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=in-app-support',
    tier: 'strategic',
  },
  {
    name: 'Opal',
    logoUrl: opalMark,
    url: 'https://opal.palash.dev/?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=in-app-support',
    tier: 'strategic',
  },
  {
    name: 'Supermetry',
    logoUrl: supermetryMark,
    url: 'https://supermetry.com/?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=in-app-support',
    tier: 'strategic',
  },
];

/**
 * Tier display order (highest first), matching the tiers in SPONSORS.md and
 * funding.json. Sponsors are grouped by tier on the Support page; any
 * unrecognized tier sorts to the end.
 * @type {string[]}
 */
export const SPONSOR_TIERS = ['strategic', 'company', 'development', 'community', 'supporter'];

/**
 * Where "Become a sponsor" and the tier docs point. Single source of truth for
 * both the Support page and the footer link.
 */
export const SPONSOR_CONTACT = {
  /** The "Sponsorship inquiry" issue form (.github/ISSUE_TEMPLATE/sponsor.yml).
   *  A bare prefilled ?title/&body link can't be used: the repo has
   *  blank_issues_enabled=false, so those redirect to the template chooser and
   *  drop the body — the template route carries the structured fields instead. */
  githubIssue: `${REPO}/issues/new?template=sponsor.yml`,
  /** Recurring or one-time support with GitHub's sponsor credit and badge. */
  githubSponsors: GITHUB_SPONSORS_URL,
  /** One-tap alternative for individuals who'd rather tip than sign a deal. */
  kofi: KOFI_URL,
  /** SPONSORS.md on GitHub — what sponsors get + the current roster. */
  docsUrl: `${REPO}/blob/main/SPONSORS.md`,
};
