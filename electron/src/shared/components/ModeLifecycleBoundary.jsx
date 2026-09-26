/**
 * Owns the DOM subtree for one top-level workspace.
 *
 * Some workspaces host imperative renderers (WaveSurfer, media elements, and
 * portals). Replacing the host when navigation changes prevents a late
 * renderer cleanup from mutating the next workspace's React-owned DOM.
 */
export default function ModeLifecycleBoundary({ mode, children }) {
  return (
    <Fragment>
      <div key={mode} className="main-content" data-mode={mode}>
        {children}
      </div>
    </Fragment>
  );
}
import { Fragment } from 'react';
