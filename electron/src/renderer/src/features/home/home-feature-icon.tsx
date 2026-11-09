/** Small native SVGs. CSS animates only the engaged card; no timers or animation runtime. */
export function HomeFeatureIcon({ destination }: { destination: string }) {
  const artwork = (() => {
    switch (destination) {
      case '/clone':
        return (
          <>
            <rect x="4" y="5" width="8" height="14" rx="4" />
            <path d="M2 15v2a6 6 0 0 0 12 0v-2M8 23v4m-3 0h6" />
            <path d="M7 9h2m-2 3h2" strokeWidth="1.3" opacity=".65" />
            <path d="M18 6h9m-3-3 3 3-3 3" strokeWidth="1.5" opacity=".6" />
            <g className="home-svg-wave home-svg-delay">
              <path d="M19 16v6m4-10v14m4-11v8" />
            </g>
          </>
        );
      case '/design':
        return (
          <>
            <g className="home-svg-tilt">
              <path d="m7 26 15-15 3 3L10 29ZM18 15l3 3" />
            </g>
            <g className="home-svg-spark">
              <path d="m11 4 1.3 3.7L16 9l-3.7 1.3L11 14l-1.3-3.7L6 9l3.7-1.3ZM25 3v5m-2.5-2.5h5" />
            </g>
          </>
        );
      case '/dub':
        return (
          <>
            <g className="home-svg-exchange">
              <path d="M4 5h15a2 2 0 0 1 2 2v9H10l-5 4v-4H4Z" />
              <path d="M8 9h9m-9 3h6" opacity=".6" />
            </g>
            <g className="home-svg-exchange home-svg-reverse">
              <path d="M24 12h4v15h-4v3l-5-3h-8v-7" />
              <path d="M16 22h8" opacity=".6" />
            </g>
          </>
        );
      case '/stories':
        return (
          <>
            <path d="M5 4v24M5 10h23M5 20h23" opacity=".35" />
            <rect className="home-svg-sequence" x="8" y="5" width="11" height="5" rx="1.5" />
            <rect
              className="home-svg-sequence home-svg-delay"
              x="15"
              y="15"
              width="12"
              height="5"
              rx="1.5"
            />
            <rect
              className="home-svg-sequence home-svg-delay-long"
              x="8"
              y="24"
              width="15"
              height="4"
              rx="1.5"
            />
          </>
        );
      case '/audiobook':
        return (
          <>
            <path d="M16 8C12 5 7 5 3 6v20c5-1 9-1 13 2 4-3 8-3 13-2V6c-4-1-9-1-13 2Zm0 0v20" />
            <path d="M7 11h5m-5 5h5m-5 5h5" opacity=".45" />
            <g className="home-svg-page">
              <path d="M20 11h5m-5 5h5m-5 5h5" />
            </g>
          </>
        );
      case '/gallery':
        return (
          <>
            <g className="home-svg-fan">
              <rect x="5" y="7" width="18" height="22" rx="3" />
            </g>
            <rect x="9" y="3" width="18" height="22" rx="3" fill="var(--card)" />
            <circle cx="18" cy="11" r="3" />
            <path d="M13 21c0-5 10-5 10 0" />
          </>
        );
      case '/transcriptions':
        return (
          <>
            <rect x="4" y="4" width="6" height="14" rx="3" />
            <path d="M2 14v2a5 5 0 0 0 10 0v-2M7 21v6m-3 0h6" />
            <path className="home-svg-sequence" d="M17 8h12" />
            <path className="home-svg-sequence home-svg-delay" d="M17 15h12" />
            <path className="home-svg-sequence home-svg-delay-long" d="M17 22h8" />
          </>
        );
      case '/workflows':
        return (
          <>
            <path d="M10 8h12v9M8 11v13h13" opacity=".4" />
            <rect className="home-svg-sequence" x="3" y="3" width="9" height="9" rx="2" />
            <rect
              className="home-svg-sequence home-svg-delay"
              x="18"
              y="13"
              width="10"
              height="9"
              rx="2"
            />
            <circle className="home-svg-sequence home-svg-delay-long" cx="24" cy="27" r="3" />
          </>
        );
      default:
        return (
          <>
            <path d="M4 8h24M4 16h24M4 24h24" opacity=".45" />
            <g className="home-svg-adjust">
              <rect x="8" y="5" width="5" height="6" rx="1.5" fill="var(--card)" />
              <rect x="8" y="21" width="5" height="6" rx="1.5" fill="var(--card)" />
            </g>
            <g className="home-svg-adjust home-svg-reverse">
              <rect x="20" y="13" width="5" height="6" rx="1.5" fill="var(--card)" />
            </g>
          </>
        );
    }
  })();
  return (
    <svg
      className="home-feature-svg"
      viewBox="0 0 32 32"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {artwork}
    </svg>
  );
}
