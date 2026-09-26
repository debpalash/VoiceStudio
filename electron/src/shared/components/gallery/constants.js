// Shared archetype facet helpers — used by ArchetypesZone + ArchetypeCard.
export const titleCase = (s) => (s ? String(s).replace(/\b\w/g, (c) => c.toUpperCase()) : s);
export const facetLabel = (v) => titleCase(String(v).replace(' pitch', '').replace(' accent', ''));

// One responsive card grid for every gallery zone: auto-filling columns with
// a fluid floor — `min(264px,100%)` (not a bare px value) so a narrow shell
// packs to a single column instead of overflowing sideways. 264px is the
// narrowest width that still fits the card's Preview + Use-voice row.
export const GALLERY_GRID =
  'gallery-cards grid grid-cols-[repeat(auto-fill,minmax(min(264px,100%),1fr))] gap-[10px]';
