// Single source of truth for VoiceStudio's sponsors + the "become a sponsor"
// contact links. Consumed by the Support page's Sponsors section and the
// footer's Sponsors link, so a logo or link change lands in exactly one place.
//
// Local-first / zero-token by design: "Become a sponsor" opens a PREFILLED
// GitHub issue (same pattern as the bug reporter, utils/bugReport.js) — the
// user reviews and submits it in their own browser; VoiceStudio never POSTs or
// holds a credential.

import { KOFI_URL } from '../utils/donateLinks';

const REPO = 'https://github.com/debpalash/VoiceStudio';

/**
 * Active sponsors. EMPTY until the first sponsor signs on — the Support page
 * renders a "be the first" placeholder while this is empty.
 *
 * To add a sponsor, append an entry here AND to SPONSORS.md (repo root) — the
 * two are kept in lockstep (in-app grid ⇄ README / project-site credit):
 *
 *   {
 *     name:    'Acme Corp',                      // display name + <img alt>
 *     logoUrl: 'https://acme.example/logo.svg',  // wide / transparent works best
 *     url:     'https://acme.example',           // where the logo links to
 *     tier:    'gold',                           // one of SPONSOR_TIERS
 *   }
 *
 * @type {{ name: string, logoUrl: string, url: string, tier: string }[]}
 */
export const SPONSORS = [];

/**
 * Tier display order (highest first). Sponsors are grouped by tier on the
 * Support page; any unrecognized tier sorts to the end.
 * @type {string[]}
 */
export const SPONSOR_TIERS = ['platinum', 'gold', 'silver', 'bronze'];

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
  /** One-tap alternative for individuals who'd rather tip than sign a deal. */
  kofi: KOFI_URL,
  /** SPONSORS.md on GitHub — what sponsors get + the current roster. */
  docsUrl: `${REPO}/blob/main/SPONSORS.md`,
};
