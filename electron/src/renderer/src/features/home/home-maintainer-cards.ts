import { WEBSITE_URL, X_URL } from '../../../../shared/utils/contactLinks.js';

/** Core team hover cards: name, role and the profiles people can follow. */
export const homeMaintainerCards: Record<
  string,
  {
    name: 'homeUi.maintainerPalash' | 'homeUi.maintainerShimul';
    role: 'homeUi.creatorCoreTeam' | 'homeUi.coreTeam';
    links: CardLink[];
  }
> = {
  debpalash: {
    name: 'homeUi.maintainerPalash',
    role: 'homeUi.creatorCoreTeam',
    links: [
      { kind: 'website', handle: 'palash.dev', href: WEBSITE_URL },
      {
        kind: 'github',
        handle: '@debpalash',
        href: 'https://github.com/debpalash',
      },
      { kind: 'x', handle: '@idebpalash', href: X_URL },
    ],
  },
  velixio: {
    name: 'homeUi.maintainerShimul',
    role: 'homeUi.coreTeam',
    links: [
      { kind: 'website', handle: 'shimul.me', href: 'https://shimul.me' },
      {
        kind: 'github',
        handle: '@velixio',
        href: 'https://github.com/velixio',
      },
      { kind: 'x', handle: '@shimuldn', href: 'https://x.com/shimuldn' },
    ],
  },
};

export type CardLink = {
  kind: 'website' | 'github' | 'x';
  handle: string;
  href: string;
};
