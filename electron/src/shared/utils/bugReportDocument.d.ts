/** A report quotes this many characters of an error message. */
export const MAX_MSG_CHARS: number;
export function composeBugReportUrl(options?: {
  title?: string;
  labels?: string;
  factsSection?: string[];
  error?: Error | string;
  ctx?: string;
  crashSection?: string[];
  diagnosticSection?: string[];
  reachabilitySection?: string[];
  breadcrumbs?: string;
}): string;
export function buildIssueSearchUrl(error: unknown): string;
