# Support page

The support page puts the monthly development goal, donation amounts, and Ko-fi / PayPal links first. Selecting an amount carries it into PayPal; Ko-fi lets you choose the amount on its own page. No checkout opens until you choose a provider.

Star and community links offer other ways to help. Sponsors remain visible. The Electron page uses the official VoiceStudio logo, a single donation panel, visible sponsor and Pro cards, and an icon grid for contact channels. No accordion hides those actions. Controls support keyboard navigation, and decorative interaction animations respect reduced-motion preferences.

Workspace headers link to Support immediately before Search. A compact, sidebar-colored footer sits below each workspace content area, outside its scrolling editor and above the agent dock. It shows Integrations, Become a Sponsor, and a right-aligned X. Integrations opens the directory. Become a Sponsor opens the partnership form, with an email draft fallback to partner@voicestudio.sh; the app never sends email itself.

The sponsor button's hover and keyboard-focus tooltip describes the placement and shows a dated GitHub snapshot: 22,678 Electron installer asset downloads across releases v0.5.3–v0.5.6, 207,236 repository views for Sep 10–23, 2026, and 35,336 stars checked Sep 25, 2026. Downloads count GitHub release asset requests, not unique installations; repository views count visits, not people. The desktop app makes no analytics request to show this tooltip. Update the snapshot and date together when revising the figures.

The footer X, workspace Get Pro shortcut, and Support page Pro card open a dedicated `/pro` page. It leads with $99 per-user yearly, $299 per-user lifetime, and Enterprise contact cards. Pro and Lifetime include user selectors, live totals, and links that preserve the selected plan and quantity on website checkout. The page then presents the proposed application-licensing, batch-rule, revision-history, delivery and preflight benefits. Recipes, watch folders and remote compute/worker/GPU-sharing features remain free and are excluded from the paid list. The Pro and Support introductions no longer imply that a purchase unlocks voice cloning. Core local generation and its AGPL commercial-use rights remain free and unlimited. Licence activation opens from the pricing section. Checkout stays disabled until the paid tools, quantity fulfilment, merchant offer, and terms pass the release gates. Commercial software distribution terms remain a separate agreement. See [the Pro specification](specs/desktop-pro-page.md).

On the Integrations page, only entries with a built-in setup show Works with VoiceStudio and capability chips; the rest are marked External link. Setup details and source links are recorded in [the directory notes](integration-directory.md).

License keys require OS-backed encryption; activation fails with a storage error
when the keyring is unavailable or Electron selects plaintext storage. Existing
legacy key files remain readable for deactivation.
