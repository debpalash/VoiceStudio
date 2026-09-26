/**
 * routingNotice — the single frontend mirror of `routing_notice()` in
 * backend/services/engine_routing.py.
 *
 * A routing verdict carries `routing_reason` on plenty of BENIGN outcomes:
 * a Windows DirectML host gets `cpu_only` + an explanatory note on a
 * perfectly normal pick (routing rule 5), and `unavailable` always carries
 * one (rule 6). Only two shapes are worth interrupting the user for:
 *
 *   - `cpu_fallback` — always; the engine silently lost its accelerator.
 *   - `accelerated` WITH a reason — a driver/arch risk or the #1226
 *     under-provisioned-VRAM caveat.
 *
 * This lived inline in engineSelectToast as `if (r?.routing_reason)`, which
 * warned on the benign cases too (Greptile P1, #1280). Now that a second
 * caller needs the same question answered (the generate-time preflight), it
 * is one exported predicate — two copies of this rule would drift, and the
 * drift is invisible until someone on the wrong hardware complains.
 */

/**
 * @param {{routing_status?: string, routing_reason?: string|null}|null} r
 * @returns {{status: string, reason: string}|null} the notice worth showing.
 */
export function routingNotice(r) {
  const status = r?.routing_status;
  const reason = r?.routing_reason;
  if (status === 'cpu_fallback') return { status, reason: reason || '' };
  if (status === 'accelerated' && reason) return { status, reason };
  return null;
}
