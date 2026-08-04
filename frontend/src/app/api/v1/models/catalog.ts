/**
 * Model catalog helpers.
 *
 * OmniRoute resolved a 278-provider catalog locally. Here the catalog is the
 * gateway's own `/v1/models`, which lists exactly the virtual models the router
 * can serve for the calling user.
 */

import { callGateway } from "@/bridge/gateway";

export type CatalogModel = { id: string; object?: string; owned_by?: string };

export async function getCatalog(init?: {
  cookie?: string | null;
  authorization?: string | null;
}): Promise<CatalogModel[]> {
  const result = await callGateway<{ data?: CatalogModel[] }>("/v1/models", {
    cookie: init?.cookie ?? null,
    authorization: init?.authorization ?? null,
  });
  if (!result.ok) return [];
  return Array.isArray(result.data?.data) ? result.data.data : [];
}

/** Paid-model hiding is an OmniRoute catalog feature with no gateway equivalent. */
export function shouldHidePaid(): boolean {
  return false;
}
