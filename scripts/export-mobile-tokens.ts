// Exports the Electron theme (palettes, type scale, radii, spacing) as native
// tokens for the iOS and Android apps, so all three clients share one source.
//
//   bun scripts/export-mobile-tokens.ts          # write generated files
//   bun scripts/export-mobile-tokens.ts --check  # fail if they are stale
//
// Sources: electron/src/renderer/src/styles/{t3-theme,globals}.css (the
// "default" palette, built from Tailwind colors and color-mix chains) and
// electron/src/renderer/src/lib/themes (the named palettes). Every color is
// resolved to sRGB here; the apps never see oklch or color-mix.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import {
  THEME_COLOR_ROLES,
  getThemeColorsForAppearance,
  type ThemeAppearance,
  type ThemeColorRole,
} from '../electron/src/renderer/src/lib/themes/t3-palettes';
import { AVAILABLE_PALETTES, colorVariable } from '../electron/src/renderer/src/lib/themes/theme-preferences';

const ROOT = join(import.meta.dir, '..');
const STYLES = join(ROOT, 'electron/src/renderer/src/styles');
const OUT = {
  json: join(ROOT, 'mobile/contract/tokens.json'),
  swift: join(ROOT, 'mobile/ios/VoiceStudioKit/Sources/VoiceStudioKit/Theme/Tokens.generated.swift'),
  kotlin: join(ROOT, 'mobile/android/core/designsystem/src/main/kotlin/com/voicestudio/mobile/designsystem/Tokens.generated.kt'),
};

type RGBA = [number, number, number, number]; // sRGB 0..1, alpha 0..1

// ---------- color parsing ----------
function oklchToSrgb(l: number, c: number, h: number): [number, number, number] {
  const hr = (h * Math.PI) / 180;
  const a = c * Math.cos(hr);
  const b = c * Math.sin(hr);
  const l_ = l + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = l - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = l - 0.0894841775 * a - 1.291485548 * b;
  const [L, M, S] = [l_ ** 3, m_ ** 3, s_ ** 3];
  const lin = [
    4.0767416621 * L - 3.3077115913 * M + 0.2309699292 * S,
    -1.2684380046 * L + 2.6097574011 * M - 0.3413193965 * S,
    -0.0041960863 * L - 0.7034186147 * M + 1.707614701 * S,
  ];
  return lin.map((v) => {
    const x = Math.min(1, Math.max(0, v));
    return x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055;
  }) as [number, number, number];
}

const num = (s: string, pctScale = 1) =>
  s === 'none' ? 0 : s.endsWith('%') ? (parseFloat(s) / 100) * pctScale : parseFloat(s);

function splitTop(s: string, sep: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let cur = '';
  for (const ch of s) {
    if (ch === '(') depth++;
    if (ch === ')') depth--;
    if (ch === sep && depth === 0) {
      out.push(cur.trim());
      cur = '';
    } else cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

function mix(a: RGBA, b: RGBA, p: number): RGBA {
  // CSS color-mix in srgb: premultiplied-alpha interpolation.
  const alpha = a[3] * p + b[3] * (1 - p);
  if (alpha === 0) return [0, 0, 0, 0];
  const ch = (i: number) => (a[i] * a[3] * p + b[i] * b[3] * (1 - p)) / alpha;
  return [ch(0), ch(1), ch(2), alpha];
}

function parseColor(raw: string, vars: Map<string, string>, seen = new Set<string>()): RGBA {
  const v = raw.trim();
  let m: RegExpMatchArray | null;
  if ((m = v.match(/^var\((--[\w-]+)\)$/))) {
    if (seen.has(m[1])) throw new Error(`cycle at ${m[1]}`);
    const ref = vars.get(m[1]);
    if (ref === undefined) throw new Error(`undefined ${m[1]}`);
    return parseColor(ref, vars, new Set([...seen, m[1]]));
  }
  if (v === 'transparent') return [0, 0, 0, 0];
  if ((m = v.match(/^#([0-9a-f]{3,8})$/i))) {
    let h = m[1];
    if (h.length <= 4) h = [...h].map((c) => c + c).join('');
    const n = (i: number) => parseInt(h.slice(i, i + 2), 16) / 255;
    return [n(0), n(2), n(4), h.length === 8 ? n(6) : 1];
  }
  if ((m = v.match(/^oklch\((.+)\)$/))) {
    const [main, alpha] = m[1].split('/').map((s) => s.trim());
    const [l, c, h] = main.split(/\s+/);
    return [...oklchToSrgb(num(l), num(c), num(h)), alpha ? num(alpha) : 1];
  }
  if ((m = v.match(/^rgba?\((.+)\)$/))) {
    const [main, alpha] = m[1].split('/').map((s) => s.trim());
    const parts = main.split(/[\s,]+/).filter(Boolean);
    return [...parts.slice(0, 3).map((p) => num(p, 255) / 255), alpha ? num(alpha) : parts[3] ? num(parts[3]) : 1] as RGBA;
  }
  if ((m = v.match(/^--alpha\((.+)\/\s*([\d.]+%)\)$/))) {
    const base = parseColor(m[1], vars, seen);
    return [base[0], base[1], base[2], base[3] * num(m[2])];
  }
  if ((m = v.match(/^color-mix\(in srgb,(.+)\)$/))) {
    const [a, b] = splitTop(m[1], ',');
    const pa = a.match(/^(.*?)\s+([\d.]+)%$/);
    const pb = b.match(/^(.*?)\s+([\d.]+)%$/);
    const p = pa ? parseFloat(pa[2]) / 100 : pb ? 1 - parseFloat(pb[2]) / 100 : 0.5;
    return mix(parseColor(pa ? pa[1] : a, vars, seen), parseColor(pb ? pb[1] : b, vars, seen), p);
  }
  throw new Error(`unsupported color: ${v}`);
}

// ---------- CSS variable extraction ----------
function decls(block: string): [string, string][] {
  const out: [string, string][] = [];
  for (const m of block.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/(--[\w-]+)\s*:\s*([^;{}]+);/g))
    out.push([m[1], m[2].replace(/\s+/g, ' ').trim()]);
  return out;
}

function blockAfter(css: string, start: number): string {
  let depth = 0;
  for (let i = css.indexOf('{', start); i < css.length; i++) {
    if (css[i] === '{') depth++;
    if (css[i] === '}' && --depth === 0) return css.slice(css.indexOf('{', start) + 1, i);
  }
  throw new Error('unbalanced CSS');
}

// Resolve like the renderer's build does, so hoisted or isolated installs both work.
const tailwind = readFileSync(Bun.resolveSync('tailwindcss/theme.css', join(ROOT, 'electron')), 'utf8');
const globals = readFileSync(join(STYLES, 'globals.css'), 'utf8');
const t3 = readFileSync(join(STYLES, 't3-theme.css'), 'utf8');

const t3Root = blockAfter(t3, t3.indexOf(':root'));
const darkIdx = t3Root.indexOf('@variant dark');
const darkBlock = blockAfter(t3Root, darkIdx);
const lightBlock = t3Root.slice(0, darkIdx);
const themeIdBlock = blockAfter(t3, t3.indexOf("html[data-theme-id]"));

function varMap(appearance: ThemeAppearance): Map<string, string> {
  const vars = new Map<string, string>();
  for (const [k, v] of decls(tailwind)) vars.set(k, v);
  for (const m of globals.matchAll(/@theme\s*\{/g)) for (const [k, v] of decls(blockAfter(globals, m.index!))) vars.set(k, v);
  for (const [k, v] of decls(blockAfter(globals, globals.indexOf(':root {')))) vars.set(k, v);
  for (const [k, v] of decls(lightBlock)) vars.set(k, v);
  if (appearance === 'dark') for (const [k, v] of decls(darkBlock)) vars.set(k, v);
  return vars;
}

// The default palette has no role table: invert the data-theme-id block
// (CSS var <- --app-theme-<role>) and fill roles that block does not map.
const ROLE_FALLBACK_VAR: Partial<Record<ThemeColorRole, string>> = {
  textMuted: '--muted-foreground',
  accent: '--primary',
  accentForeground: '--primary-foreground',
  terminalScrollbar: '--muted-foreground',
  terminalScrollbarHover: '--foreground',
};
const roleToCssVar = new Map<string, string>();
for (const [cssVar, value] of decls(themeIdBlock)) {
  const m = value.match(/^var\((--app-theme-[\w-]+)\)$/);
  if (m && !roleToCssVar.has(m[1])) roleToCssVar.set(m[1], cssVar);
}

const hex = ([r, g, b, a]: RGBA) =>
  '#' + [r, g, b, a].map((x) => Math.round(Math.min(1, Math.max(0, x)) * 255).toString(16).padStart(2, '0')).join('');

type Palette = { id: string; label: string; colors: Partial<Record<ThemeAppearance, Record<string, string>>> };
const palettes: Palette[] = [];

const defaults: Palette = { id: 'default', label: 'Default', colors: {} };
for (const appearance of ['light', 'dark'] as const) {
  const vars = varMap(appearance);
  const colors: Record<string, string> = {};
  for (const role of THEME_COLOR_ROLES) {
    const cssVar = roleToCssVar.get(colorVariable(role)) ?? ROLE_FALLBACK_VAR[role];
    if (!cssVar) throw new Error(`default palette: no CSS variable for role ${role}`);
    colors[role] = hex(parseColor(`var(${cssVar})`, vars));
  }
  defaults.colors[appearance] = colors;
}
palettes.push(defaults);

const empty = new Map<string, string>();
for (const theme of AVAILABLE_PALETTES) {
  const p: Palette = { id: theme.id, label: theme.label, colors: {} };
  for (const appearance of ['light', 'dark'] as const) {
    const c = getThemeColorsForAppearance(theme, appearance);
    if (!c) continue;
    p.colors[appearance] = Object.fromEntries(THEME_COLOR_ROLES.map((r) => [r, hex(parseColor(c[r], empty))]));
  }
  palettes.push(p);
}

// ---------- non-color tokens ----------
const light = varMap('light');
const rem = (v: string) => Math.round(parseFloat(v) * 16 * 100) / 100;
const radius = rem(light.get('--radius')!);
const tokens = {
  $comment: 'GENERATED by scripts/export-mobile-tokens.ts from the Electron theme. Do not edit.',
  defaultMode: 'dark',
  palettes,
  font: { family: 'Inter', fallback: 'system', mono: 'system-monospace' },
  // Desktop sizes in points; apps scale them with Dynamic Type / font scale.
  text: Object.fromEntries(
    ['ui', 'label', 'caption', 'editor'].map((k) => [
      k,
      { size: rem(light.get(`--text-${k}`)!), lineHeight: rem(light.get(`--text-${k}--line-height`)!) },
    ]),
  ),
  radius: {
    sm: radius - 4,
    md: radius - 2,
    lg: radius,
    xl: Math.round(radius * 1.4 * 100) / 100,
    '2xl': Math.round(radius * 1.8 * 100) / 100,
    control: rem(light.get('--control-radius')!),
  },
  spacing: { gap: rem(light.get('--workspace-gap')!), inset: rem(light.get('--workspace-inset')!) },
  ios: {
    cardRadius: rem(light.get('--ios-card-radius')!),
    fieldRadius: rem(light.get('--ios-field-radius')!),
    homeWordmarkSize: rem(light.get('--ios-home-wordmark-size')!),
  },
};

// ---------- emitters ----------
const header = '// GENERATED by scripts/export-mobile-tokens.ts from the Electron theme. Do not edit.\n';
const ident = (s: string) => s.replace(/-(\w)/g, (_, c) => c.toUpperCase()).replace(/^(\d)/, '_$1');
const typeName = (s: string) => ident(s).replace(/^\w/, (c) => c.toUpperCase());

function swift(): string {
  const rgba = (h: string) => {
    const n = (i: number) => (parseInt(h.slice(i, i + 2), 16) / 255).toFixed(4);
    return `RGBA(${n(1)}, ${n(3)}, ${n(5)}, ${n(7)})`;
  };
  let s = header + 'import Foundation\n\npublic enum VSTokens {\n';
  s += '    public struct RGBA: Sendable, Equatable { public let r, g, b, a: Double\n        public init(_ r: Double, _ g: Double, _ b: Double, _ a: Double) { self.r = r; self.g = g; self.b = b; self.a = a } }\n\n';
  s += '    public struct Palette: Sendable { public let id: String; public let label: String; public let light: [String: RGBA]?; public let dark: [String: RGBA]? }\n\n';
  s += `    public static let roles: [String] = [${THEME_COLOR_ROLES.map((r) => `"${r}"`).join(', ')}]\n\n`;
  s += '    public static let palettes: [Palette] = [\n';
  for (const p of palettes) {
    const dict = (c?: Record<string, string>) =>
      c ? `[\n${Object.entries(c).map(([k, v]) => `            "${k}": ${rgba(v)},`).join('\n')}\n        ]` : 'nil';
    s += `        Palette(id: "${p.id}", label: "${p.label}", light: ${dict(p.colors.light)}, dark: ${dict(p.colors.dark)}),\n`;
  }
  s += '    ]\n\n';
  for (const [k, v] of Object.entries(tokens.text))
    s += `    public static let text${typeName(k)} = (size: ${v.size.toFixed(2)}, lineHeight: ${v.lineHeight.toFixed(2)})\n`;
  for (const [k, v] of Object.entries(tokens.radius)) s += `    public static let radius${typeName(k)}: Double = ${v.toFixed(2)}\n`;
  for (const [k, v] of Object.entries(tokens.spacing)) s += `    public static let spacing${typeName(k)}: Double = ${v.toFixed(2)}\n`;
  for (const [k, v] of Object.entries(tokens.ios)) s += `    public static let ios${typeName(k)}: Double = ${v.toFixed(2)}\n`;
  return s + '}\n';
}

function kotlin(): string {
  const argb = (h: string) => `0x${(h.slice(7, 9) + h.slice(1, 7)).toUpperCase()}`;
  let s = header + 'package com.voicestudio.mobile.designsystem\n\nobject VSTokens {\n';
  s += '    data class Palette(val id: String, val label: String, val light: Map<String, Long>?, val dark: Map<String, Long>?)\n\n';
  s += `    val roles: List<String> = listOf(${THEME_COLOR_ROLES.map((r) => `"${r}"`).join(', ')})\n\n`;
  s += '    val palettes: List<Palette> = listOf(\n';
  for (const p of palettes) {
    const map = (c?: Record<string, string>) =>
      c ? `mapOf(\n${Object.entries(c).map(([k, v]) => `            "${k}" to ${argb(v)},`).join('\n')}\n        )` : 'null';
    s += `        Palette("${p.id}", "${p.label}", ${map(p.colors.light)}, ${map(p.colors.dark)}),\n`;
  }
  s += '    )\n\n';
  for (const [k, v] of Object.entries(tokens.text)) {
    s += `    const val TEXT_${k.toUpperCase()}_SIZE: Float = ${v.size.toFixed(2)}f\n`;
    s += `    const val TEXT_${k.toUpperCase()}_LINE_HEIGHT: Float = ${v.lineHeight.toFixed(2)}f\n`;
  }
  for (const [k, v] of Object.entries(tokens.radius)) s += `    const val RADIUS_${k.toUpperCase()}: Float = ${v.toFixed(2)}f\n`;
  for (const [k, v] of Object.entries(tokens.spacing)) s += `    const val SPACING_${k.toUpperCase()}: Float = ${v.toFixed(2)}f\n`;
  for (const [k, v] of Object.entries(tokens.ios)) s += `    const val IOS_${k.replace(/[A-Z]/g, (c) => '_' + c).toUpperCase()}: Float = ${v.toFixed(2)}f\n`;
  return s + '}\n';
}

const outputs: [string, string][] = [
  [OUT.json, JSON.stringify(tokens, null, 2) + '\n'],
  [OUT.swift, swift()],
  [OUT.kotlin, kotlin()],
];

if (process.argv.includes('--check')) {
  const stale = outputs.filter(([path, body]) => {
    try {
      return readFileSync(path, 'utf8') !== body;
    } catch {
      return true;
    }
  });
  if (stale.length) {
    console.error('Stale mobile tokens; run `bun scripts/export-mobile-tokens.ts`:\n' + stale.map(([p]) => '  ' + p).join('\n'));
    process.exit(1);
  }
} else {
  for (const [path, body] of outputs) {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, body);
  }
}
