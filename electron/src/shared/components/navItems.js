/**
 * Workspace navigation model — shared by both navigation presentations.
 *
 * The icon rail (`NavRail`) and the titlebar tab strip (`TitleTabs`) are two
 * skins of the SAME list; Settings → Appearance → Navigation style picks which
 * one renders. Declaring the workspaces once here means a new workspace shows
 * up in both skins from a single edit and the two can't drift apart.
 *
 * `accent` is the workspace's hue. Both skins spend it the same way: the
 * ACTIVE item wears it (rail indicator bar / tab top rule + icon), inactive
 * items stay neutral, so the chrome never turns into a colour chart.
 */
import {
  Globe,
  Fingerprint,
  Film,
  FolderOpen,
  Settings2,
  Library,
  FileText,
  BookOpen,
  BookMarked,
  Boxes,
} from 'lucide-react';

/** The workspaces, in navigation order. `tKey` resolves as `nav.<tKey>`. */
export const NAV_ITEMS = [
  { id: 'launchpad', Icon: Globe, tKey: 'launchpad', accent: '#f3a5b6' },
  { id: 'studio', Icon: Fingerprint, tKey: 'voice', accent: '#d3869b' },
  { id: 'dub', Icon: Film, tKey: 'dub', accent: '#fe8019' },
  { id: 'stories', Icon: BookOpen, tKey: 'stories', accent: '#fabd2f' },
  { id: 'audiobook', Icon: BookMarked, tKey: 'audiobook', accent: '#8ec07c' },
  { id: 'gallery', Icon: Library, tKey: 'gallery', accent: '#b8bb26' },
  { id: 'transcriptions', Icon: FileText, tKey: 'transcripts', accent: '#d3869b' },
  { id: 'projects', Icon: FolderOpen, tKey: 'omnidrive', accent: '#83a598' },
  // Engines + model weights. Sits last in the workspace list (immediately above
  // Settings in the footer) because it configures the app rather than producing
  // anything — it is where the defaults every other workspace runs on are set.
  { id: 'catalogue', Icon: Boxes, tKey: 'catalogue', accent: '#689d6a' },
];

/** Pinned to the end of the nav (rail footer / tab-strip tail). */
export const NAV_FOOTER_ITEMS = [
  { id: 'settings', Icon: Settings2, tKey: 'settings', accent: '#fabd2f' },
];
