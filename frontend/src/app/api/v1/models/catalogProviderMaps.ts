/**
 * Combo alias resolution.
 *
 * OmniRoute resolved combo step targets against a local catalog of provider and
 * model aliases. Combos land in Phase 1 of OMNIROUTE_INTEGRATION.md; until the
 * gateway owns that catalog these resolve identity-style, so a target is used
 * exactly as written.
 */

export type AliasMaps = {
  providerAliasToId: Record<string, string>;
  modelAliasToId: Record<string, string>;
};

export function buildAliasMaps(): AliasMaps {
  return { providerAliasToId: {}, modelAliasToId: {} };
}

export function getComboTargetModelId(_maps: AliasMaps, target: unknown): string | null {
  if (typeof target === "string") return target;
  if (target && typeof target === "object") {
    const record = target as Record<string, unknown>;
    const value = record.model ?? record.modelId ?? record.id;
    if (typeof value === "string") return value;
  }
  return null;
}
