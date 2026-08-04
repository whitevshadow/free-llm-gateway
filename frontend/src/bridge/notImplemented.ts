/**
 * The "not wired yet" contract.
 *
 * OmniRoute's dashboard covers far more surface than this gateway implements.
 * Rather than delete ~190 pages, they are kept and their data calls answered
 * with a uniform, machine-readable envelope. A page that gets this back can
 * render an honest empty state; nothing silently shows zeros as if they were
 * real measurements, which is the failure mode worth avoiding — a "0 requests"
 * chart and a "no backend" chart must not look the same.
 *
 * As backend features land, add the route to routes.ts and the path stops
 * reaching this file.
 */

export const NOT_IMPLEMENTED_CODE = "gateway_route_not_implemented";

export type NotImplementedBody = {
  error: string;
  code: typeof NOT_IMPLEMENTED_CODE;
  notImplemented: true;
  path: string;
  method: string;
  /** Which integration phase covers this, when it is known. */
  plannedPhase: string | null;
  docs: string;
  /** Empty collections under the keys OmniRoute pages commonly destructure, so
   *  a page that ignores the error at least renders empty instead of crashing
   *  on `undefined.map`. */
  items: never[];
  data: never[];
  results: never[];
  total: 0;
};

/**
 * Best-effort mapping from an unimplemented path to the phase of
 * OMNIROUTE_INTEGRATION.md that would deliver it. Purely informational — it
 * makes the empty state say "Phase 3" instead of "unavailable".
 */
const PHASE_BY_PREFIX: Array<[string, string]> = [
  ["combos", "Phase 1 — Combos"],
  ["auto-combo", "Phase 1 — Combos"],
  ["fallback", "Phase 2 — Routing strategies"],
  ["free-tier", "Phase 3 — Quota ledger"],
  ["quota", "Phase 3 — Quota ledger"],
  ["free-provider-rankings", "Phase 3 — Quota ledger"],
  ["health", "Phase 4 — Resilience"],
  ["chaos", "Phase 4 — Resilience"],
  ["analytics", "Phase 6 — Dashboard pages"],
  ["mcp", "Tier 3 — parked"],
  ["a2a", "Tier 3 — parked"],
  ["acp", "Tier 3 — parked"],
  ["memory", "Tier 3 — parked"],
  ["gamification", "Tier 3 — parked"],
  ["agent-skills", "Tier 3 — parked"],
  ["cli-agents", "Tier 3 — parked"],
  ["cli-tools", "Tier 3 — parked"],
  ["cloud-agents", "Tier 3 — parked"],
  ["batch", "Tier 3 — parked"],
  ["translator", "Tier 3 — parked"],
  ["plugins", "Tier 3 — parked"],
  ["webhooks", "Tier 2 — Webhooks + audit"],
  ["evals", "Tier 3 — parked"],
];

export function phaseFor(segments: string[]): string | null {
  const head = segments[0] ?? "";
  for (const [prefix, phase] of PHASE_BY_PREFIX) {
    if (head === prefix) return phase;
  }
  return null;
}

export function notImplementedBody(segments: string[], method: string): NotImplementedBody {
  const path = `/api/${segments.join("/")}`;
  return {
    error: `${method} ${path} is not implemented by this gateway yet.`,
    code: NOT_IMPLEMENTED_CODE,
    notImplemented: true,
    path,
    method,
    plannedPhase: phaseFor(segments),
    docs: "OMNIROUTE_INTEGRATION.md",
    items: [],
    data: [],
    results: [],
    total: 0,
  };
}
