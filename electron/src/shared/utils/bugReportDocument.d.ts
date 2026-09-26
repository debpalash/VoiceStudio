export function composeBugReportUrl(options?: {
  title?: string;
  error?: Error | string;
  ctx?: string;
  crashSection?: string[];
  diagnosticSection?: string[];
  reachabilitySection?: string[];
  breadcrumbs?: string;
}): string;
export function buildIssueSearchUrl(error: unknown): string;
