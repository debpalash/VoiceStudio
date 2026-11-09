/**
 * Keep React rendering when something outside React rewrote the page (#3168).
 *
 * Browser page translation (Chrome/Edge "Translate this page", used by people
 * whose language the app catalog lacks) and some extensions replace React's
 * text nodes with their own `<font>` wrappers. React still holds the old
 * nodes, so its next `insertBefore` / `removeChild` names a node that is no
 * longer a child and the whole UI crashes with "Failed to execute
 * 'insertBefore' on 'Node'". This is React's long-standing advice
 * (facebook/react#11538): when the reference node has moved, insert at the
 * end instead of throwing, and treat removing an already-moved node as done.
 * Calls whose nodes are where React expects take the native path unchanged.
 */
let installed = false;

export function installForeignDomGuard(target: typeof Node | undefined = globalThis.Node): boolean {
  if (installed || typeof target !== 'function' || !target.prototype) return false;
  installed = true;
  const proto = target.prototype;
  const nativeRemoveChild = proto.removeChild;
  const nativeInsertBefore = proto.insertBefore;
  let warned = false;
  const warn = () => {
    if (warned) return;
    warned = true;
    console.warn(
      'VoiceStudio: the page was changed outside the app (often browser translation); recovering.',
    );
  };
  proto.removeChild = function removeChild<T extends Node>(this: Node, child: T): T {
    if (child.parentNode !== this) {
      warn();
      // Already detached or moved into a translation wrapper: drop it from
      // wherever it went so React's view of the tree stays true.
      child.parentNode?.removeChild(child);
      return child;
    }
    return nativeRemoveChild.call(this, child) as T;
  };
  proto.insertBefore = function insertBefore<T extends Node>(
    this: Node,
    node: T,
    child: Node | null,
  ): T {
    if (child && child.parentNode !== this) {
      warn();
      return nativeInsertBefore.call(this, node, null) as T;
    }
    return nativeInsertBefore.call(this, node, child) as T;
  };
  return true;
}

/** Test seam: forget that the guard was installed. */
export function _resetForeignDomGuardForTests() {
  installed = false;
}
