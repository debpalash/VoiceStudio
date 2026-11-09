/**
 * Ask the user to confirm an action.
 *
 * The browser prompt only supports its standard OK/Cancel controls, so callers
 * must provide a message that remains clear when title and label options cannot
 * be displayed.
 */
export async function askConfirm(message, title = 'Confirm', opts = {}) {
  void title;
  void opts;
  return window.confirm(message);
}
