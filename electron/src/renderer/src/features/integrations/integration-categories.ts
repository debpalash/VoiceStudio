/**
 * Directory categories, in filter order. Every category the catalog uses has
 * its own label key under `integrationCatalog.category`; never borrow a key
 * from another feature (a borrowed key renders that feature's word, e.g. a
 * calling integration labelled "Dubbing").
 */
export const INTEGRATION_CATEGORIES = [
  'comms',
  'automation',
  'agents',
  'mcp',
  'developer',
  'data',
  'productivity',
] as const;

export type IntegrationCategory = (typeof INTEGRATION_CATEGORIES)[number];

export function isIntegrationCategory(value: string): value is IntegrationCategory {
  return (INTEGRATION_CATEGORIES as readonly string[]).includes(value);
}

/** The i18n key for a category label; unknown categories get the generic label. */
export function integrationCategoryKey(category: string | null | undefined): string {
  return category && isIntegrationCategory(category)
    ? `integrationCatalog.category.${category}`
    : 'integrationCatalog.category.other';
}
