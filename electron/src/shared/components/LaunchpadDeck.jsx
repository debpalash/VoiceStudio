import React, { useState } from 'react';
import { ArrowUpRight } from 'lucide-react';

// Card min-track (px) handed to the grid's `repeat(auto-fit, minmax(min, 1fr))`.
// This floor is the ONLY responsive knob: the browser derives the column count
// from the grid's OWN rendered width (= the shell's own width under the
// `zoom: --ui-scale` model), so columns reflow 7→…→1 with zero viewport @media.
// A wider floor on narrow shells yields fewer, comfier columns.
const CARD_MIN_WIDE = '200px';
const CARD_MIN_NARROW = '240px';

/**
 * FeatureCard — one launchpad feature tile in the full-width grid. The card is
 * borderless (every `--chrome-border` token is zeroed app-wide), so it reads as
 * three quiet bands — glyph + count, title + arrow, description — on a
 * whisper-faint surface. `--card-hue` (inline) is the tile's only colour and is
 * spent sparingly: the glyph at rest, the surface/count/arrow once raised.
 * Hover OR keyboard focus raises the card (`lp-action-card--raised`) — the
 * raise is class-driven from React state so pointer and keyboard share one code
 * path and tests can assert it. The arrow is decoration (aria-hidden); the
 * button's accessible name stays title + desc. `--lp-i` staggers the entrance.
 */
function FeatureCard({ hue, Icon, title, desc, count, onClick, index, raised, onRaise, onSettle }) {
  return (
    <button
      type="button"
      className={`lp-action-card lp-animate${raised === index ? ' lp-action-card--raised' : ''}`}
      style={{ '--card-hue': hue, '--lp-i': index }}
      onClick={onClick}
      onMouseEnter={() => onRaise(index)}
      onFocus={() => onRaise(index)}
      onBlur={onSettle}
    >
      <span className="card-head">
        <span className="card-icon">
          <Icon size={17} strokeWidth={1.5} />
        </span>
        {count > 0 && <span className="card-count">{count}</span>}
      </span>
      <span className="card-title-row">
        <h3>{title}</h3>
        <span className="card-go" aria-hidden="true" data-testid="launchpad-card-open">
          <ArrowUpRight size={13} strokeWidth={1.75} />
        </span>
      </span>
      <p className="card-desc">{desc}</p>
    </button>
  );
}

/**
 * LaunchpadDeck — the launchpad's seven feature cards as a full-width,
 * responsive grid. PR #904 fanned them into a fixed ~780px deck that left dead
 * margins in a maximized window; this fills the content width instead and
 * reflows its column count (7→…→1 from a maximized ~2560px display down to the
 * 900×600 minimum) via `repeat(auto-fit, minmax(--lp-card-min, 1fr))` — the
 * same container-driven mechanism the rest of the launchpad uses, never a
 * viewport @media (which fires at the wrong width whenever --ui-scale ≠ 1).
 * `narrow` (the app-container's own width class, via useShellNarrow) only
 * widens the card floor so narrow shells get fewer, comfier columns. Which card
 * is raised by hover/focus lives here in React so keyboard focus shares the
 * exact same forward-lift as the pointer and tests can assert it.
 */
export default function LaunchpadDeck({ features, narrow = false }) {
  const [raised, setRaised] = useState(null);
  return (
    <div
      className="lp-cards"
      style={{ '--lp-card-min': narrow ? CARD_MIN_NARROW : CARD_MIN_WIDE }}
      onMouseLeave={() => setRaised(null)}
    >
      {features.map((f, i) => (
        <FeatureCard
          key={f.key}
          index={i}
          hue={f.hue}
          Icon={f.Icon}
          title={f.title}
          desc={f.desc}
          count={f.count}
          onClick={f.go}
          raised={raised}
          onRaise={setRaised}
          onSettle={() => setRaised(null)}
        />
      ))}
    </div>
  );
}
