# Sponsor artwork

The README wordmarks have matching light and dark variants. Their symbols come
from the projects' own repositories, with names set in the same system-font
treatment as the existing Supermetry wordmark:

- [OpenGTM mark](https://github.com/debpalash/OpenGTM/blob/main/apps/web/public/opengtm-mark-v8.svg)
- [Opal mark](https://github.com/debpalash/Opal/blob/main/assets/logo.svg)
- [Supermetry brand](https://github.com/velixio/supermetry/tree/main/docs/brand)

The app imports the bundled symbols from
[`electron/src/shared/assets/sponsors/`](../../electron/src/shared/assets/sponsors/).
Displaying sponsor recognition does not load remote images or call sponsor
services. Sponsor websites open only when the user follows their links.

Keep the roster in the English and translated READMEs, [SPONSORS.md](../../SPONSORS.md)
and [`sponsors.js`](../../electron/src/shared/config/sponsors.js) together;
`tests/test_sponsors_lockstep.py` checks the names, destinations and local assets.

README logo credits open the sponsor GitHub repositories with the existing
`readme-logo` UTM campaign tags. The in-app Support page and SPONSORS.md use
website destinations with their own campaign tags. The roster and bundled
assets are checked together by `tests/test_sponsors_lockstep.py`.
