export interface Breadcrumb {
  t: number;
  action: string;
}

export function addBreadcrumb(action: string): void;
export function getBreadcrumbs(): Breadcrumb[];
export function formatBreadcrumbs(): string;
export function clearBreadcrumbs(): void;
