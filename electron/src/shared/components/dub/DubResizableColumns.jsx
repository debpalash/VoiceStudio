import { Children, useRef, useState } from 'react';

const STORAGE_KEY = 'omnivoice.dubSplit.v1';
const MIN_LEFT = 25;
const MAX_LEFT = 70;
const KEYBOARD_STEP = 5;

const clamp = (value) => Math.min(MAX_LEFT, Math.max(MIN_LEFT, Math.round(value)));

const loadRatio = () => {
  if (typeof window === 'undefined') return 44;
  try {
    const stored = Number.parseFloat(localStorage.getItem(STORAGE_KEY) ?? '');
    return Number.isFinite(stored) ? clamp(stored) : 44;
  } catch {
    return 44;
  }
};

export default function DubResizableColumns({ children, resizeLabel }) {
  const [leftRatio, setLeftRatio] = useState(loadRatio);
  const ratioRef = useRef(leftRatio);
  const draggingRef = useRef(false);
  const columns = Children.toArray(children);

  const isRtl = (element) =>
    element.closest('[dir]')?.getAttribute('dir') === 'rtl' ||
    document.documentElement.dir === 'rtl';

  const updateRatio = (nextRatio, persist = false) => {
    const next = clamp(nextRatio);
    ratioRef.current = next;
    setLeftRatio(next);
    if (persist) {
      try {
        localStorage.setItem(STORAGE_KEY, String(next));
      } catch {
        // Storage can be disabled; resizing still works for this session.
      }
    }
  };

  const ratioFromPointer = (event) => {
    const bounds = event.currentTarget.parentElement?.getBoundingClientRect();
    if (!bounds?.width) return ratioRef.current;
    const offset = isRtl(event.currentTarget)
      ? (bounds.right - event.clientX) / bounds.width
      : (event.clientX - bounds.left) / bounds.width;
    return offset * 100;
  };

  const handlePointerDown = (event) => {
    draggingRef.current = true;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    updateRatio(ratioFromPointer(event));
  };

  const handlePointerMove = (event) => {
    if (draggingRef.current) updateRatio(ratioFromPointer(event));
  };

  const finishPointerResize = (event) => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    updateRatio(ratioRef.current, true);
  };

  const handleKeyDown = (event) => {
    let next;
    const direction = isRtl(event.currentTarget) ? -1 : 1;
    if (event.key === 'ArrowLeft') next = ratioRef.current - KEYBOARD_STEP * direction;
    if (event.key === 'ArrowRight') next = ratioRef.current + KEYBOARD_STEP * direction;
    if (event.key === 'Home') next = MIN_LEFT;
    if (event.key === 'End') next = MAX_LEFT;
    if (next === undefined) return;
    event.preventDefault();
    updateRatio(next, true);
  };

  return (
    <div
      className="dub-editor-grid dub-resizable-columns flex-1 min-h-0 min-w-0 overflow-hidden"
      style={{ gridTemplateColumns: `${leftRatio}fr 12px ${100 - leftRatio}fr` }}
    >
      {columns[0]}
      <div
        className="dub-column-splitter"
        role="separator"
        aria-label={resizeLabel}
        aria-orientation="vertical"
        aria-valuemin={MIN_LEFT}
        aria-valuemax={MAX_LEFT}
        aria-valuenow={leftRatio}
        tabIndex={0}
        onKeyDown={handleKeyDown}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishPointerResize}
        onPointerCancel={finishPointerResize}
      />
      {columns[1]}
    </div>
  );
}
