/** Move the item with id `fromId` to sit immediately before `toId`. Pure. */
export function reorder(list, fromId, toId) {
  if (fromId === toId) return list.slice();
  const from = list.findIndex((x) => x.id === fromId);
  const to = list.findIndex((x) => x.id === toId);
  if (from < 0 || to < 0) return list.slice();
  const next = list.slice();
  const [moved] = next.splice(from, 1);
  const insertAt = next.findIndex((x) => x.id === toId);
  next.splice(insertAt, 0, moved);
  return next;
}
