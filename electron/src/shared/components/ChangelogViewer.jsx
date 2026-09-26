// "What's new" changelog reader (feat/safe-updates).
// Renders the app's own CHANGELOG.md (parsed by the backend into
// {version, date, intro, sections:[{title, bullets}]}) as an accordion:
// newest release expanded, older ones collapsed. Bullets go through the safe
// markdown-lite inline renderer — bold leads and (#NNN) refs stay plain text.
import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { InlineMd } from './MarkdownLite';

export default function ChangelogViewer({ releases }) {
  const [expanded, setExpanded] = useState(() =>
    releases && releases.length ? releases[0].version : null,
  );
  if (!Array.isArray(releases) || releases.length === 0) return null;
  return (
    <div className="changelog-viewer" data-testid="changelog-viewer">
      {releases.map((rel) => {
        const open = expanded === rel.version;
        return (
          <div key={rel.version} className={`changelog-viewer__rel ${open ? 'is-open' : ''}`}>
            <button
              type="button"
              className="changelog-viewer__head"
              aria-expanded={open}
              onClick={() => setExpanded(open ? null : rel.version)}
              data-testid={`changelog-toggle-${rel.version}`}
            >
              {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              <span className="changelog-viewer__ver">v{rel.version}</span>
              {rel.date && <span className="changelog-viewer__date">{rel.date}</span>}
            </button>
            {open && (
              <div className="changelog-viewer__body" data-testid={`changelog-body-${rel.version}`}>
                {rel.intro && (
                  <p className="changelog-viewer__intro">
                    <InlineMd text={rel.intro} />
                  </p>
                )}
                {(rel.sections || []).map((sec, i) => (
                  <div key={`${sec.title}-${i}`} className="changelog-viewer__section">
                    {sec.title && <div className="changelog-viewer__sec-title">{sec.title}</div>}
                    <ul className="changelog-viewer__bullets">
                      {sec.bullets.map((b, j) => (
                        <li key={j}>
                          <InlineMd text={b} />
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
