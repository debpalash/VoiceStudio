# Support page

The support page puts the monthly development goal, donation amounts, and Ko-fi / PayPal links first. Selecting an amount carries it into PayPal; Ko-fi lets you choose the amount on its own page. No checkout opens until you choose a provider.

Star and community links offer other ways to help. Sponsors remain visible. The Electron page uses the official VoiceStudio logo, a single donation panel, visible sponsor and Pro cards, and an icon grid for contact channels. No accordion hides those actions. Controls support keyboard navigation, and decorative interaction animations respect reduced-motion preferences.

Workspace headers link to Support immediately before Search. A sponsor footer sits below each workspace content area, outside its scrolling editor and above the agent dock. It reads the shared sponsor roster, uses themed hover/focus tooltips, and opens each logo's integration page inside the app (the catalog page when a logo has no detail page); vendor websites are one click further, on that page. With an empty roster, one combined “Your logo here” booking tile demonstrates the placement and opens the sponsorship message form. It prepares a mailto draft to partner@voicestudio.sh in the default email app, or copies the address; it never sends email itself.

The sponsor-bar remove control opens the Free vs Pro comparison on Support. The comparison lists the proposed Pro benefits: no telemetry, a hideable sponsor bar, a Pro badge, and advanced tools. Activation verification and the specific advanced-tool list are not configured; no Pro entitlement is inferred from donations or local preferences.

Sponsor tiles form a left-aligned, horizontally scrolling row with 1px gaps. The rightmost combined logo-plus tile opens the booking form.

Logo hover cards match their trigger tile width and grow vertically to fit their contents.

The footer chevron opens a searchable sponsor catalog above the strip. Cards show logos, names, tiers, and websites, and open the in-app integration page; a booking card opens the email form. The panel scrolls within 60% of the viewport. Escape or the close button collapses it and returns focus to the chevron.

The expanded catalog is labeled Integrations. Entries from the sponsored roster display a Featured badge in their catalog card and hover card; the empty booking preview does not.

Ten voice-AI company examples populate the catalog and compact strip using locally bundled official icons. The compact strip labels them Directory example, not Featured. On the Integrations page, only entries with a built-in setup show Works with VoiceStudio and capability chips; the rest are marked External link. Setup details and source links are recorded in [the directory notes](integration-directory.md).
