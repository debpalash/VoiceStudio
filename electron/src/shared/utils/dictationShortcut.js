import { detectPlatform } from './micError';

const DEFAULT_SHORTCUT = 'CmdOrCtrl+Shift+Space';

function keyCode(part) {
  if (/^[a-z]$/i.test(part)) return `Key${part.toUpperCase()}`;
  if (/^[0-9]$/.test(part)) return `Digit${part}`;
  const aliases = {
    Return: 'Enter',
    Esc: 'Escape',
    PageUp: 'PageUp',
    PageDown: 'PageDown',
  };
  return aliases[part] || part;
}

export function parseShortcut(accelerator = DEFAULT_SHORTCUT, platform = detectPlatform()) {
  const parsed = {
    accelerator,
    alt: false,
    ctrl: false,
    meta: false,
    shift: false,
    code: '',
    modifierCodes: new Set(),
  };

  for (const rawPart of accelerator
    .split('+')
    .map((part) => part.trim())
    .filter(Boolean)) {
    switch (rawPart.toLowerCase()) {
      case 'cmdorctrl':
      case 'commandorcontrol':
        if (platform === 'mac') {
          parsed.meta = true;
          parsed.modifierCodes.add('Meta');
        } else {
          parsed.ctrl = true;
          parsed.modifierCodes.add('Control');
        }
        break;
      case 'cmd':
      case 'command':
      case 'meta':
      case 'super':
        parsed.meta = true;
        parsed.modifierCodes.add('Meta');
        break;
      case 'ctrl':
      case 'control':
        parsed.ctrl = true;
        parsed.modifierCodes.add('Control');
        break;
      case 'alt':
      case 'option':
        parsed.alt = true;
        parsed.modifierCodes.add('Alt');
        break;
      case 'shift':
        parsed.shift = true;
        parsed.modifierCodes.add('Shift');
        break;
      default:
        if (parsed.code) return null;
        parsed.code = keyCode(rawPart);
    }
  }
  return parsed.code ? parsed : null;
}

export function eventMatchesShortcut(event, accelerator, platform = detectPlatform()) {
  const shortcut = parseShortcut(accelerator, platform);
  return Boolean(
    shortcut &&
    event.code === shortcut.code &&
    Boolean(event.altKey) === shortcut.alt &&
    Boolean(event.ctrlKey) === shortcut.ctrl &&
    Boolean(event.metaKey) === shortcut.meta &&
    Boolean(event.shiftKey) === shortcut.shift,
  );
}

export function isShortcutRelease(event, shortcut) {
  if (!shortcut) return false;
  if (event.code === shortcut.code) return true;
  for (const modifier of shortcut.modifierCodes) {
    if (
      event.code === modifier ||
      event.code === `${modifier}Left` ||
      event.code === `${modifier}Right`
    )
      return true;
  }
  return false;
}

export function formatShortcut(accelerator, platform = detectPlatform()) {
  const parts = accelerator
    .split('+')
    .map((part) => part.trim())
    .filter(Boolean);
  if (platform !== 'mac') {
    return parts
      .map((part) => {
        if (/^(cmdorctrl|commandorcontrol|ctrl|control)$/i.test(part)) return 'Ctrl';
        if (/^(cmd|command|meta|super)$/i.test(part)) return 'Super';
        if (/^(alt|option)$/i.test(part)) return 'Alt';
        if (/^shift$/i.test(part)) return 'Shift';
        return part;
      })
      .join('+');
  }
  const glyphs = {
    cmdorctrl: '⌘',
    commandorcontrol: '⌘',
    cmd: '⌘',
    command: '⌘',
    meta: '⌘',
    super: '⌘',
    ctrl: '⌃',
    control: '⌃',
    alt: '⌥',
    option: '⌥',
    shift: '⇧',
  };
  return parts.map((part) => glyphs[part.toLowerCase()] || part).join('');
}

export { DEFAULT_SHORTCUT };
