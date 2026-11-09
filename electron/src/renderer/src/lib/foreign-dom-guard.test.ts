import { afterEach, expect, it, vi } from 'vitest';
import { _resetForeignDomGuardForTests, installForeignDomGuard } from './foreign-dom-guard';

const nativeRemoveChild = Node.prototype.removeChild;
const nativeInsertBefore = Node.prototype.insertBefore;
afterEach(() => {
  Node.prototype.removeChild = nativeRemoveChild;
  Node.prototype.insertBefore = nativeInsertBefore;
  _resetForeignDomGuardForTests();
  vi.restoreAllMocks();
});

// What page translation does: wrap a text node React owns in a <font>.
function translated() {
  const parent = document.createElement('div');
  const text = document.createTextNode('Hello');
  parent.appendChild(text);
  const font = document.createElement('font');
  parent.replaceChild(font, text);
  font.appendChild(text);
  return { parent, text, font };
}

it('throws without the guard when React inserts before a translated node (#3168)', () => {
  const { parent, text } = translated();
  expect(() => parent.insertBefore(document.createElement('span'), text)).toThrow();
  expect(() => parent.removeChild(text)).toThrow();
});

it('recovers from translated nodes and leaves ordinary DOM calls unchanged', () => {
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  expect(installForeignDomGuard()).toBe(true);
  expect(installForeignDomGuard()).toBe(false);
  const { parent, text, font } = translated();

  const span = document.createElement('span');
  expect(parent.insertBefore(span, text)).toBe(span);
  expect(parent.lastChild).toBe(span);

  expect(parent.removeChild(text)).toBe(text);
  expect(text.parentNode).toBeNull();
  expect(font.childNodes).toHaveLength(0);

  const first = document.createElement('b');
  parent.insertBefore(first, font);
  expect(parent.firstChild).toBe(first);
  parent.removeChild(first);
  expect(first.parentNode).toBeNull();
  expect(console.warn).toHaveBeenCalledTimes(1);
});
