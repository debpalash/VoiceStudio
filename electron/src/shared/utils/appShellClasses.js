/**
 * Class list for the app-shell grid (`.app-container`).
 *
 * Extracted from App.jsx because the combinations decide the GRID TEMPLATE —
 * get one wrong and a column is reserved for something that isn't rendered,
 * which shows up as a dead black gutter rather than as an error. Pure function,
 * so `utils/appShellClasses.test.js` can pin every combination.
 */

/**
 * @param {object} o
 * @param {'rail'|'tabs'} o.navStyle        Navigation skin (Settings → Appearance).
 * @param {'left'|'right'} o.navRailSide    Which edge the icon rail sits on.
 * @param {boolean} o.isSidebarCollapsed    Projects/history panel collapsed to icons.
 * @param {boolean} o.hideSidebar           Current view hides that panel entirely.
 * @param {string} [o.shellSizeClass]       '', 'shell-narrow' or 'shell-mini'.
 * @returns {string} space-separated class list
 */
export function appShellClasses({
  navStyle,
  navRailSide,
  isSidebarCollapsed,
  hideSidebar,
  shellSizeClass = '',
}) {
  return [
    'app-container',
    isSidebarCollapsed ? 'sidebar-collapsed' : '',
    hideSidebar ? 'sidebar-hidden' : '',
    // `rail-right` reshuffles the grid columns around a rail that, in tabs
    // mode, isn't rendered at all — it would reserve 48px for nothing.
    navStyle !== 'tabs' && navRailSide === 'right' ? 'rail-right' : '',
    navStyle === 'tabs' ? 'nav-tabs' : '',
    shellSizeClass,
  ]
    .filter(Boolean)
    .join(' ');
}

export default appShellClasses;
