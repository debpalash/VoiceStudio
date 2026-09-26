import React from 'react';

/**
 * VoiceStudio's compact mark: a rounded speech waveform with one small spark.
 * The desktop icon uses the same silhouette on a dark tile; app chrome keeps
 * it transparent so it stays crisp at titlebar sizes and in every theme.
 */
export default function VoiceStudioMark({ className = '', title, ...props }) {
  return (
    <svg
      viewBox="0 0 64 64"
      fill="none"
      className={className}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      {...props}
    >
      {title ? <title>{title}</title> : null}
      <path
        d="M6 34c4 0 5-7 9-7 5 0 4 14 9 14 5 0 4-23 9-23s4 28 9 28 4-21 9-21c4 0 5 9 8 9"
        stroke="currentColor"
        strokeWidth="5.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M50 9c.7 4.3 3.7 7.3 8 8-4.3.7-7.3 3.7-8 8-.7-4.3-3.7-7.3-8-8 4.3-.7 7.3-3.7 8-8Z"
        fill="currentColor"
        opacity="0.72"
      />
    </svg>
  );
}
