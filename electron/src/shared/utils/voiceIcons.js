/**
 * voiceIcons — lucide icon maps for the design tab's preset / personality
 * chips and the demo-preset grid, replacing the old emoji glyphs. Keyed by the
 * stable backend/constants id so labels stay in i18n and only the glyph swaps.
 */
import {
  Megaphone,
  Baby,
  Wind,
  PartyPopper,
  BookOpen,
  Flame,
  Smile,
  Tv,
  Wand2,
  Briefcase,
  Zap,
  Headphones,
  Skull,
  Mic,
  Moon,
  Sparkles,
  UserSquare2,
} from 'lucide-react';

// PRESETS (utils/constants.js) — quick design starting points.
export const PRESET_ICONS = {
  narrator: Megaphone, // "Authoritative"
  excited_child: Baby,
  anxious_whisper: Wind,
  surprised_woman: PartyPopper,
  elderly_story: BookOpen,
  sichuan: Flame,
};

// Personality presets (backend personalities.py, is_demo=false) — chip strip.
export const PERSONALITY_ICONS = {
  narrator: BookOpen,
  casual: Smile,
  news_anchor: Tv,
  storyteller: Wand2,
  corporate: Briefcase,
  energetic: Zap,
};

// Demo presets (backend personalities.py, is_demo=true) — empty-state cards.
export const DEMO_ICONS = {
  audiobook_uk_narrator: BookOpen,
  us_news_anchor: Tv,
  indian_support_agent: Headphones,
  gravelly_villain: Skull,
  aussie_podcaster: Mic,
  bedtime_storyteller: Moon,
  mandarin_sichuan: Flame,
};

export const FALLBACK_VOICE_ICON = Sparkles;
export const FALLBACK_PERSONALITY_ICON = UserSquare2;

/**
 * Strip emoji/pictographs from an i18n label (CJK text passes through),
 * collapsing the leftover whitespace. Lets us drop emoji from any locale's
 * string without editing every locale file — the lucide icon renders instead.
 */
export const stripVoiceEmoji = (s) =>
  (s || '')
    .replace(/[\p{Extended_Pictographic}️‍]/gu, '')
    .replace(/\s+/g, ' ')
    .trim();
