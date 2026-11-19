export interface InstalledBrowser {
  id: string;
  name: string;
}
export interface SiteBrowserState {
  url: string;
  title: string;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  error: boolean;
}
export interface SiteBrowserBridge {
  open(url: string): Promise<void>;
  close(): Promise<void>;
  state(): Promise<SiteBrowserState | null>;
  navigate(url: string): Promise<void>;
  command(action: 'back' | 'forward' | 'reload' | 'stop'): Promise<void>;
  bounds(rect: SiteBrowserBounds): Promise<void>;
  installed(): Promise<InstalledBrowser[]>;
  openExternal(id: string): Promise<void>;
  onState(callback: (state: SiteBrowserState | null) => void): () => void;
}

export interface SiteBrowserBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function browserUrl(input: unknown): string {
  if (typeof input !== 'string' || !input.trim() || input.length > 8192)
    throw new Error('Invalid URL');
  const value = input.trim();
  const hostWithPort = /^[^:/?#\s]+:\d+(?:[/?#]|$)/.test(value);
  const hasScheme =
    /^https?:/i.test(value) || (!hostWithPort && /^[a-z][a-z\d+.-]*:/i.test(value));
  return webTargetUrl(hasScheme ? value : `https://${value}`);
}

/** Page-originated targets (links, redirects, popups) are absolute URLs; address-bar shortcuts never apply. */
export function webTargetUrl(input: unknown): string {
  if (typeof input !== 'string' || input.length > 8192) throw new Error('Invalid URL');
  const url = new URL(input);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password)
    throw new Error('Only HTTP and HTTPS websites are supported');
  return url.href;
}
