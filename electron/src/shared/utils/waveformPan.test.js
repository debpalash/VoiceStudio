import { afterEach, expect, it, vi } from 'vitest';
import { bindWaveformPan } from './waveformPan';

let cleanup;
afterEach(() => {
  cleanup?.();
  document.body.replaceChildren();
});

function setup() {
  const scroller = document.createElement('div');
  const wrapper = document.createElement('div');
  scroller.append(wrapper);
  document.body.append(scroller);
  Object.defineProperties(scroller, { scrollWidth: { value: 2000 }, clientWidth: { value: 500 } });
  scroller.scrollLeft = 300;
  const seek = vi.fn();
  wrapper.addEventListener('click', seek);
  cleanup = bindWaveformPan(wrapper);
  const pointer = (type, x, options = {}) => {
    const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, ...options });
    Object.defineProperty(event, 'pointerId', { value: 1 });
    wrapper.dispatchEvent(event);
  };
  return { scroller, wrapper, seek, pointer };
}

it('pans in either direction, clamps to the edges, and does not seek after dragging', () => {
  const { scroller, wrapper, seek, pointer } = setup();
  pointer('pointerdown', 200);
  pointer('pointermove', 100);
  expect(scroller.scrollLeft).toBe(400);
  expect(wrapper.style.cursor).toBe('grabbing');
  pointer('pointermove', 250);
  expect(scroller.scrollLeft).toBe(250);
  pointer('pointermove', 900);
  expect(scroller.scrollLeft).toBe(0);
  pointer('pointermove', -2000);
  expect(scroller.scrollLeft).toBe(1500);
  pointer('pointerup', 100);
  wrapper.click();
  expect(seek).not.toHaveBeenCalled();
  expect(wrapper.style.cursor).toBe('grab');
});

it('preserves click-to-seek, including small pointer jitter and a click after a drag', () => {
  const { scroller, wrapper, seek, pointer } = setup();
  pointer('pointerdown', 200);
  pointer('pointermove', 100);
  pointer('pointerup', 100);
  wrapper.click();
  pointer('pointerdown', 200);
  pointer('pointermove', 202);
  pointer('pointerup', 202);
  wrapper.click();
  expect(seek).toHaveBeenCalledTimes(1);
  expect(scroller.scrollLeft).toBe(400);
});

it('ends on cancellation, ignores secondary buttons, and removes handlers on cleanup', () => {
  const { scroller, wrapper, pointer } = setup();
  pointer('pointerdown', 200, { button: 2 });
  pointer('pointermove', 100);
  expect(scroller.scrollLeft).toBe(300);
  pointer('pointerdown', 200);
  pointer('pointercancel', 200);
  pointer('pointermove', 100);
  expect(scroller.scrollLeft).toBe(300);
  pointer('pointerdown', 200);
  cleanup();
  pointer('pointermove', 100);
  expect(scroller.scrollLeft).toBe(300);
  expect(wrapper.style.cursor).toBe('');
});
