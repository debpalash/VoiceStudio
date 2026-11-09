# Support page

The support page puts the monthly development goal, donation amounts, and Ko-fi / PayPal links first. Selecting an amount carries it into PayPal; Ko-fi lets you choose the amount on its own page. No checkout opens until you choose a provider.

Star and community links offer other ways to help. Sponsors remain visible. The Electron page uses the official VoiceStudio logo, a single donation panel, visible sponsor and Pro cards, and an icon grid for contact channels. No accordion hides those actions. Controls support keyboard navigation, and decorative interaction animations respect reduced-motion preferences.

Workspace headers link to Support immediately before Search. Integrations is reachable from the sidebar and opens the directory; sponsored entries carry a Featured badge on their card. There is no app-wide footer, partnership form or sponsor tooltip; sponsor and contact details live on this page and in Settings.


The workspace Get Pro shortcut, and Support page Pro card open a dedicated `/pro` page. It leads with $99 per-user yearly, $199 per-user lifetime, and Enterprise contact cards. Pro and Lifetime include user selectors, live totals, and links that preserve the selected plan and quantity on website checkout. The page then presents the proposed application-licensing, batch-rule, revision-history, delivery and preflight benefits. Remote compute devices, remote workers and GPU sharing are VoiceStudio Pro features and are no longer included in the open-source app. Recipes, watch folders and core local generation remain free and unlimited; the Pro and Support introductions do not imply that a purchase unlocks voice cloning. The open-source page does not expose licence activation; that flow belongs to the separate Pro distribution. Checkout stays disabled until the paid tools, quantity fulfilment, merchant offer, and terms pass the release gates. Commercial software distribution terms remain a separate agreement. See [the Pro specification](specs/desktop-pro-page.md).

On the Integrations page, only entries with a built-in setup show Works with VoiceStudio and capability chips; the rest are marked External link. Setup details and source links are recorded in [the directory notes](integration-directory.md).

License keys require OS-backed encryption; activation fails with a storage error
when the keyring is unavailable or Electron selects plaintext storage. Existing
legacy key files remain readable for deactivation.
