// ─────────────────────────────────────────────────────────────────
//  VoiceStudio design-system barrel
//
//  Import from here, not the individual files:
//    import { Button, Panel, Field, Input, Select, Dialog, Slider, Badge } from '../ui';
//  (No '@/' alias is configured in vite.config.js — use a relative path.)
//
//  Tokens are imported once here so any file that reaches for a primitive
//  also pulls in the full token scale. The token foundation (formerly
//  ui/tokens.css + ui/themes.css) was consolidated into src/index.css (P5),
//  so importing it here preserves that side-effect for every primitive
//  consumer.
// ─────────────────────────────────────────────────────────────────

import '../index.css';

export { default as Button } from './Button.jsx';
export { default as Panel } from './Panel.jsx';
export { Field, Input, Textarea, Select } from './Input.jsx';
export { default as Dialog } from './Dialog.jsx';
export { default as Slider } from './Slider.jsx';
export { default as Badge } from './Badge.jsx';
export { default as Tabs } from './Tabs.jsx';
export { default as Segmented } from './Segmented.jsx';
export { default as Tooltip } from './Tooltip.jsx';
export { default as Progress } from './Progress.jsx';
export { default as Menu } from './Menu.jsx';
export { default as Table } from './Table.jsx';
