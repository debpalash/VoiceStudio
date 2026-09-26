export interface IntegrationCatalogEntry {
  name: string;
  url: string;
  logoUrl: string;
  category: string;
  featured: boolean;
}

export const INTEGRATION_CATALOG: IntegrationCatalogEntry[];
export function integrationSlug(name: string): string;
export function getIntegrationBySlug(slug: string): IntegrationCatalogEntry | undefined;
