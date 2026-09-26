import React from 'react';

/**
 * Pip — the kawaii goal-bar mascot. A tiny rounded blob with a soft face that
 * perches on the fill of the GoalBar and waves. `currentColor` drives every
 * stroke/fill so the parent can tint Pip with the accent simply by setting
 * `color` (e.g. `style={{ color: 'var(--chrome-accent)' }}`).
 *
 * Idle animation (pipBob + pipWave) lives in DonateGoal.css and is disabled
 * under `prefers-reduced-motion`. Purely decorative → aria-hidden.
 */
export default function Pip({ size = 22, className = '', waving = true }) {
  return (
    <span
      className={`pip ${waving ? 'pip--waving' : ''} ${className}`.trim()}
      aria-hidden="true"
      style={{ width: size, height: size }}
    >
      <svg viewBox="0 0 32 32" width={size} height={size} fill="none" role="presentation">
        {/* soft glow halo */}
        <ellipse cx="16" cy="17" rx="11" ry="10.5" fill="currentColor" opacity="0.16" />
        {/* body */}
        <path
          className="pip__body"
          d="M16 4.5c-5.4 0-9.3 3.9-9.3 9.4 0 4.2 2.1 7.4 5 9.2.9.6 1.3 1.6 1.1 2.6l-.2 1c-.2.9.5 1.7 1.4 1.7h3.9c.9 0 1.6-.8 1.4-1.7l-.2-1c-.2-1 .2-2 1.1-2.6 2.9-1.8 5-5 5-9.2 0-5.5-3.9-9.4-9.2-9.4Z"
          fill="currentColor"
          opacity="0.92"
        />
        {/* face plate (cut-out so eyes read on dark bg) */}
        <ellipse cx="16" cy="14.5" rx="6.6" ry="6" fill="#0f1011" opacity="0.9" />
        {/* eyes */}
        <circle className="pip__eye" cx="13.4" cy="14" r="1.25" fill="currentColor" />
        <circle className="pip__eye" cx="18.6" cy="14" r="1.25" fill="currentColor" />
        {/* blush */}
        <circle cx="11.4" cy="16.4" r="1.1" fill="currentColor" opacity="0.45" />
        <circle cx="20.6" cy="16.4" r="1.1" fill="currentColor" opacity="0.45" />
        {/* smile */}
        <path
          d="M14 16.8c.6.7 1.3 1 2 1s1.4-.3 2-1"
          stroke="currentColor"
          strokeWidth="1.1"
          strokeLinecap="round"
          fill="none"
        />
        {/* waving arm */}
        <path
          className="pip__arm"
          d="M24.6 11.2c1.3-1.1 2.6-1.6 3.6-1.4"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          fill="none"
        />
      </svg>
    </span>
  );
}
