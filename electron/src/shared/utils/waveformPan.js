// Bind to WaveSurfer's waveform wrapper, leaving minimap and segment editing
// gestures alone. A click still seeks; a horizontal drag only pans.
export function bindWaveformPan(wrapper) {
  const scroller = wrapper.parentElement;
  if (!scroller) return () => {};
  const doc = wrapper.ownerDocument;
  const oldCursor = wrapper.style.cursor;
  const oldTouchAction = wrapper.style.touchAction;
  let gesture = null;
  let suppressClick = false;
  wrapper.style.cursor = 'grab';
  wrapper.style.touchAction = 'pan-y';

  const down = (event) => {
    if (event.button !== 0 || event.isPrimary === false) return;
    suppressClick = false;
    if (scroller.scrollWidth <= scroller.clientWidth) return;
    gesture = {
      id: event.pointerId,
      x: event.clientX,
      scroll: scroller.scrollLeft,
      dragging: false,
    };
  };
  const move = (event) => {
    if (!gesture || gesture.id !== event.pointerId) return;
    const dx = event.clientX - gesture.x;
    if (!gesture.dragging && Math.abs(dx) < 5) return;
    if (!gesture.dragging) {
      gesture.dragging = true;
      wrapper.setPointerCapture?.(event.pointerId);
      wrapper.style.cursor = 'grabbing';
    }
    event.preventDefault();
    suppressClick = true;
    scroller.scrollLeft = Math.max(
      0,
      Math.min(scroller.scrollWidth - scroller.clientWidth, gesture.scroll - dx),
    );
  };
  const finish = (event) => {
    if (!gesture || gesture.id !== event.pointerId) return;
    const id = gesture.id;
    gesture = null;
    wrapper.style.cursor = 'grab';
    if (wrapper.hasPointerCapture?.(id)) wrapper.releasePointerCapture(id);
  };
  const click = (event) => {
    if (!suppressClick) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  };
  wrapper.addEventListener('pointerdown', down);
  wrapper.addEventListener('click', click, true);
  wrapper.addEventListener('lostpointercapture', finish);
  doc.addEventListener('pointermove', move, { passive: false });
  doc.addEventListener('pointerup', finish);
  doc.addEventListener('pointercancel', finish);
  return () => {
    if (gesture) finish({ pointerId: gesture.id });
    wrapper.removeEventListener('pointerdown', down);
    wrapper.removeEventListener('click', click, true);
    wrapper.removeEventListener('lostpointercapture', finish);
    doc.removeEventListener('pointermove', move);
    doc.removeEventListener('pointerup', finish);
    doc.removeEventListener('pointercancel', finish);
    wrapper.style.cursor = oldCursor;
    wrapper.style.touchAction = oldTouchAction;
  };
}
