# `ui/` — VoiceStudio design system

The one place new UI is built from. Primitives, tokens, motion, all here.

Before you inline another `style={{}}` or reach for `.input-base`, look here first.

## Import pattern

```jsx
import { Button, Panel, Field, Input, Textarea, Select, Dialog, Slider, Badge } from '../ui';
```

Always import from the barrel, never the individual files. Tokens come along for the ride.

## Primitives

### `Tabs`

```jsx
<Tabs
  items={[
    { id: 'models',  label: 'Models',  icon: Cpu,        accent: '#f3a5b6' },
    { id: 'logs',    label: 'Logs',    icon: FileText,   accent: '#fabd2f' },
  ]}
  value={activeTab}
  onChange={setActiveTab}
/>

<Tabs variant="underline" items={…} value={…} onChange={…} />
```

Props: `items` ( `{id, label, icon?, accent?}` ) · `value` · `onChange(id)` · `size` ( `sm` | `md` ) · `variant` ( `pill` | `underline` ).

### `Segmented`

```jsx
<Segmented
  size="xs"
  value={uiScale}
  onChange={setUiScale}
  items={[
    { value: 1,   label: 'S' },
    { value: 1.3, label: 'M' },
    { value: 1.5, label: 'L' },
  ]}
/>
```

Props: `items` ( `{value, label, title?}` ) · `value` · `onChange(value)` · `size` ( `xs` | `sm` ).

### `Menu`

```jsx
import { Menu, Button } from '../ui';
import { Pencil, Copy, Trash2 } from 'lucide-react';

<Menu
  placement="bottom-end"
  items={[
    { id: 'rename',    label: 'Rename',    icon: Pencil, onSelect: onRename, shortcut: '⌘R' },
    { id: 'duplicate', label: 'Duplicate', icon: Copy,   onSelect: onDup },
    'separator',
    { id: 'delete',    label: 'Delete',    icon: Trash2, destructive: true, onSelect: onDelete },
  ]}
>
  <Button variant="icon" iconSize="sm">…</Button>
</Menu>
```

- Wraps exactly one child (the trigger). Click opens, click-outside / ESC / Tab closes.
- Keyboard: ↑↓ navigate, Home/End jump, Enter / Space selects, ESC returns focus to trigger.
- `role=menu` / `role=menuitem` / `aria-haspopup` / `aria-expanded` / `aria-disabled` wired.
- Rendered via Portal so panels and `overflow: hidden` containers don't clip it.

Item shape: `'separator'` | `{ id, label, icon?, shortcut?, destructive?, disabled?, trailing?, onSelect }`.

Props: `items` · `placement` (`bottom-start` | `bottom-end` | `top-start` | `top-end`) · `width` · `disabled` · `open` + `onOpenChange` (controlled mode).

### `Tooltip`

```jsx
<Tooltip content="Reset all shortcuts" placement="bottom">
  <Button variant="ghost" size="sm">Reset</Button>
</Tooltip>
```

Keyboard accessible (focus triggers, ESC dismisses). Wraps exactly one child. Props: `content` · `placement` ( `top` | `bottom` | `left` | `right` ) · `delay`.

### `Button`

```jsx
<Button variant="primary">Generate</Button>
<Button variant="subtle" size="sm" leading={<Play size={10} />}>Preview</Button>
<Button variant="danger" onClick={onDelete}>Delete</Button>
<Button variant="ghost">Cancel</Button>
<Button variant="chip"   active={isPicked} onClick={toggle}>female</Button>
<Button variant="preset" active={isActive}>Expert Narrator</Button>
<Button variant="icon"   iconSize="sm" aria-label="Close"><X size={10} /></Button>
<Button variant="primary" loading>Generating…</Button>
<Button block variant="primary">Full width</Button>
```

Props: `variant` · `size` · `iconSize` · `active` · `loading` · `leading` · `trailing` · `block` + all native `<button>` attrs.

### `Panel`

```jsx
<Panel title="Voice" actions={<Button variant="ghost" size="sm">Swap</Button>}>
  …
</Panel>

<Panel variant="flat"  padding="sm">…</Panel>
<Panel variant="solid" padding="md">…</Panel>
```

Props: `variant` ( `glass` | `solid` | `flat` ) · `padding` ( `none` | `sm` | `md` | `lg` ) · `title` · `actions` · `as`.

### `Field` + `Input` / `Textarea` / `Select`

```jsx
<Field label="Name" hint="Your project's display name">
  <Input value={name} onChange={e => setName(e.target.value)} placeholder="Untitled" />
</Field>

<Field label="Language" error={err}>
  <Select value={lang} onChange={e => setLang(e.target.value)}>
    <option value="en">English</option>
  </Select>
</Field>

<Textarea rows={4} placeholder="Style prompt…" />
```

Props on `Input`/`Textarea`/`Select`: `size` ( `sm` | `md` | `lg` ) + all native attrs.
Props on `Field`: `label` · `hint` · `error` · `icon`. `Field` auto-wires `id`, `aria-invalid`, and `aria-describedby`.

### `Dialog`

```jsx
<Dialog
  open={open}
  onClose={() => setOpen(false)}
  title="Compare voices"
  size="lg"
  footer={
    <>
      <Button variant="ghost" onClick={close}>Cancel</Button>
      <Button variant="primary" onClick={save}>Save</Button>
    </>
  }
>
  …
</Dialog>
```

Props: `open` · `onClose` · `title` · `footer` · `size` ( `sm` | `md` | `lg` | `xl` ) · `dismissable`.
Handles focus return, ESC close, backdrop click, `role=dialog` automatically.

### `Slider`

```jsx
<Slider
  value={gain}
  onChange={setGain}
  min={0}
  max={200}
  step={1}
  format={v => `${v}%`}
  label="Volume"
/>
```

Props: `value` · `onChange(n)` (receives a number, not an event) · `min` · `max` · `step` · `format` · `label` · `size` · `showValue`.

### `Badge`

```jsx
<Badge tone="success" dot>Ready</Badge>
<Badge tone="warn">Loading…</Badge>
<Badge tone="violet">Preset</Badge>
```

Props: `tone` ( `neutral` | `brand` | `success` | `warn` | `danger` | `info` | `violet` ) · `size` ( `xs` | `sm` ) · `dot`.

## Tokens

Every design decision is a token in [`tokens.css`](./tokens.css). Colour, spacing, radius, type scale, motion, z-index. Use them.

```css
/* GOOD */
.thing { padding: var(--space-4); background: var(--color-bg-elev-2); }

/* BAD */
.thing { padding: 8px; background: rgba(0,0,0,0.3); }
```

Import the barrel (`import '../ui'`) once in `main.jsx` and the tokens load for the whole app.

## Do / Don't

### ✅ Do

- Reach for a primitive first. If it doesn't fit, extend the primitive before writing a one-off.
- Use tokens. `var(--space-4)`, `var(--color-brand)`, `var(--radius-md)` — every time.
- Compose primitives. A toolbar is `<Panel variant="flat" padding="sm">` with a row of `<Button variant="icon">`.
- Pass ARIA / keyboard props through — primitives forward all native attrs.

### 🚫 Don't

- **No inline `style={{}}` on text, colour, spacing, or radius.** Layout-specific things (grid-template, align) are fine; visual things are not.
- **No raw hex codes in components.** If a colour isn't in tokens, add it to tokens first.
- **No new `*.css` files at the component level for purely visual concerns.** Extend primitives or add a utility class in the design system.
- **No rolling your own modal / toggle / dropdown.** Use the primitive. If it's missing a variant, add it here.

## Adding a primitive

1. It must be used in **≥3 places** before becoming a primitive.
2. It must expose a `variant` or `size` prop — primitives are **polymorphic**, not specific.
3. It ships with:
   - JSX file (`forwardRef` when wrapping a native input/button)
   - Scoped CSS file using tokens only
   - README entry (here)
   - Barrel export in `index.js`
4. Motion + focus + disabled + keyboard states are **non-negotiable**.

## Roadmap

Shipped: Button, Panel, Field/Input/Textarea/Select, Dialog, Slider, Badge.

Next (after first migration waves prove these):

- `Menu` / `Popover` — right-click + dropdown. Replaces ad-hoc absolute-positioned lists.
- `Tooltip` — keyboard-accessible replacement for `title=`.
- `Tabs` — formalises the current `.tabs` / `.tab` pattern.
- `Toast` (wrap react-hot-toast with branded defaults).
- `Progress` — wraps `.progress-container` / `.progress-fill`.
- `Table` — virtualised table primitive; the dub segment table is the first caller.

## Governance

Design-system changes go through a **design audit** (see `ROADMAP.md` Design track). Any commit that touches a primitive's *visual* behaviour requires a before/after screenshot in the PR description.
