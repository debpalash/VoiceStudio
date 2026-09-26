// Icon system for the Voice Gallery — lucide SVGs (not emoji, which render
// inconsistently across OSes), country flags for accents, a per-category color
// scale, and a CSS-animated "now playing" equalizer.
import React from 'react';
import {
  BookOpen,
  MessagesSquare,
  MessageSquare,
  Drama,
  Smartphone,
  Tv,
  Megaphone,
  GraduationCap,
  Library,
  Mic,
  Moon,
  Wand2,
  Smile,
  Headphones,
  Coffee,
  Skull,
  Bird,
  Shield,
  Sparkles,
  Ghost,
  Radio,
  Zap,
  Video,
  Trophy,
  Clapperboard,
  Gem,
  Music,
  Lightbulb,
  Globe,
} from 'lucide-react';
import US from 'country-flag-icons/react/3x2/US';
import GB from 'country-flag-icons/react/3x2/GB';
import AU from 'country-flag-icons/react/3x2/AU';
import CA from 'country-flag-icons/react/3x2/CA';
import IN from 'country-flag-icons/react/3x2/IN';
import CN from 'country-flag-icons/react/3x2/CN';
import JP from 'country-flag-icons/react/3x2/JP';
import KR from 'country-flag-icons/react/3x2/KR';
import PT from 'country-flag-icons/react/3x2/PT';
import RU from 'country-flag-icons/react/3x2/RU';

// lucide component name → component (icon identity comes from the backend).
const ICONS = {
  BookOpen,
  MessagesSquare,
  MessageSquare,
  Drama,
  Smartphone,
  Tv,
  Megaphone,
  GraduationCap,
  Library,
  Mic,
  Moon,
  Wand2,
  Smile,
  Headphones,
  Coffee,
  Skull,
  Bird,
  Shield,
  Sparkles,
  Ghost,
  Radio,
  Zap,
  Video,
  Trophy,
  Clapperboard,
  Gem,
  Music,
  Lightbulb,
  Globe,
};

// One accent color per use-case (gruvbox palette, matches the app theme).
export const USE_CASE_COLOR = {
  narration: '#83a598',
  conversational: '#8ec07c',
  characters: '#d3869b',
  social: '#fe8019',
  entertainment: '#fabd2f',
  advertisement: '#fb4934',
  informative: '#b8bb26',
};

const FLAGS = {
  'american accent': US,
  'british accent': GB,
  'australian accent': AU,
  'canadian accent': CA,
  'indian accent': IN,
  'chinese accent': CN,
  'japanese accent': JP,
  'korean accent': KR,
  'portuguese accent': PT,
  'russian accent': RU,
};

function tint(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

export function ArchetypeIcon({ name, size = 18, color }) {
  const Cmp = ICONS[name] || Sparkles;
  // Stroke weight is governed globally (`svg.lucide` in index.css) so all
  // icons share one refined weight — no per-icon strokeWidth here.
  return <Cmp size={size} color={color} />;
}

/** Country flag for an accent (or the Chinese flag for dialect voices, or a globe). */
export function AccentFlag({ accent, lang, size = 14 }) {
  let Flag = accent ? FLAGS[accent] : null;
  if (!Flag && lang === 'Chinese') Flag = CN;
  if (!Flag) return <Globe size={size} className="flag-globe" />;
  return <Flag className="accent-flag" style={{ width: size, height: Math.round(size * 0.75) }} />;
}

/** Animated equalizer shown on the card whose preview is playing (bars in CSS). */
export function NowPlaying({ color }) {
  return (
    <span className="now-playing" style={color ? { color } : undefined} aria-hidden="true">
      <i />
      <i />
      <i />
      <i />
    </span>
  );
}

/** Color-coded icon tile. Accent flags live in the metadata row, where they
 * remain readable and do not duplicate into a clipped badge under the tile. */
export function ArchetypeAvatar({ item, size = 44 }) {
  const color = USE_CASE_COLOR[item.use_case] || '#83a598';
  return (
    <div
      className="arch-avatar"
      style={{
        width: size,
        height: size,
        background: tint(color, 0.14),
      }}
    >
      <ArchetypeIcon name={item.icon} size={Math.round(size * 0.46)} color={color} />
    </div>
  );
}
