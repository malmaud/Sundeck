import type { Game } from "./types";

export function computeAutoSyncIds(games: Game[], count: number, excludedIds: Set<number>): Set<number> {
  const ids = new Set<number>();
  let n = 0;
  for (const g of games) {
    if (excludedIds.has(g.app_id)) continue;
    if (n >= count) break;
    ids.add(g.app_id);
    n++;
  }
  return ids;
}
