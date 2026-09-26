export async function invoke(): Promise<never> {
  throw new Error('Retired Tauri API invoked');
}
export async function listen(): Promise<() => void> {
  return () => {};
}
export async function emit(): Promise<void> {}
export async function getVersion(): Promise<string> {
  return '0.0.0-test';
}
export function getCurrentWindow() {
  return { close: async () => {}, setFocus: async () => {}, startDragging: async () => {} };
}
export async function openUrl(): Promise<void> {}
export async function revealItemInDir(): Promise<void> {}
export async function open(): Promise<null> {
  return null;
}
export async function confirm(): Promise<boolean> {
  return false;
}
export async function ask(): Promise<boolean> {
  return false;
}
export async function relaunch(): Promise<void> {}
export function getCurrentWebview() {
  return { setZoom: async () => {} };
}
