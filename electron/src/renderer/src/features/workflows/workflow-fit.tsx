import { useEffect, useRef } from 'react';
import {
  getViewportForBounds,
  useNodesInitialized,
  useReactFlow,
  useStoreApi,
  type Rect,
  type Viewport,
} from '@xyflow/react';

/** Gap kept above the first step, so a workflow reads from the top like a page. */
const TOP_GAP = 40;

/**
 * A workflow opens with every step in view, never past its natural size, and
 * starting near the top: fitted like `fitView`, but a short workflow is not
 * pushed to the vertical middle of a tall canvas.
 */
export function openingViewport(bounds: Rect, width: number, height: number): Viewport {
  const fitted = getViewportForBounds(bounds, width, height, 0.2, 1, 0.1);
  return { ...fitted, y: Math.min(fitted.y, TOP_GAP - bounds.y * fitted.zoom) };
}

/** Frames the open workflow once its steps are measured; the canvas remounts per workflow. */
export function FitWorkflowOnOpen() {
  const measured = useNodesInitialized();
  const flow = useReactFlow();
  const store = useStoreApi();
  const done = useRef(false);
  useEffect(() => {
    if (!measured || done.current) return;
    const { width, height } = store.getState();
    const nodes = flow.getNodes();
    if (!width || !height || !nodes.length) return;
    done.current = true;
    void flow.setViewport(openingViewport(flow.getNodesBounds(nodes), width, height));
  }, [flow, measured, store]);
  return null;
}
