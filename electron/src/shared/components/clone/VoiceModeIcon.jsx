import './VoiceModeIcon.css';

export default function VoiceModeIcon({ mode }) {
  return (
    <svg
      className={`voice-mode-icon voice-mode-icon--${mode}`}
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {mode === 'audio' ? (
        [4, 8, 12, 16, 20].map((x, index) => (
          <path
            key={x}
            className="voice-mode-wave"
            style={{ animationDelay: `${index * 35}ms` }}
            d={`M${x} ${[9, 6, 3, 6, 9][index]}v${[6, 12, 18, 12, 6][index]}`}
          />
        ))
      ) : mode === 'design' ? (
        <>
          <path d="M4 7h16M4 17h16" />
          <circle className="voice-mode-dial" cx="9" cy="7" r="2.5" fill="var(--chrome-bg)" />
          <circle
            className="voice-mode-dial voice-mode-dial--second"
            cx="15"
            cy="17"
            r="2.5"
            fill="var(--chrome-bg)"
          />
        </>
      ) : mode === 'upload' ? (
        <>
          <path d="M5 15v5h14v-5" />
          <path className="voice-mode-upload" d="M12 16V3m-4 4 4-4 4 4" />
        </>
      ) : mode === 'record' ? (
        <>
          <rect x="9" y="3" width="6" height="12" rx="3" />
          <path className="voice-mode-mic" d="M6 11v1a6 6 0 0 0 12 0v-1" />
          <path d="M12 18v3m-3 0h6" />
        </>
      ) : (
        <>
          <path className="voice-mode-arrow" d="M4 7h15m-4-4 4 4-4 4" />
          <path className="voice-mode-arrow voice-mode-arrow--back" d="M20 17H5m4-4-4 4 4 4" />
        </>
      )}
    </svg>
  );
}
