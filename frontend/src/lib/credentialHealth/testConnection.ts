/**
 * In-process connection test, used by the credential-health scheduler.
 *
 * This used to live in `src/app/api/providers/[id]/test/route.ts`. That file was
 * a leftover OmniRoute route handler, and because Next.js prefers a concrete
 * segment over the `[...path]` catch-all, it SHADOWED the bridge's own
 * `providers/:id/test` entry (see bridge/routes.ts) — so the Test button on the
 * providers page never reached the mapping that was written for it.
 *
 * It also addressed the wrong resource. A "connection" in this UI is one
 * CREDENTIAL, and the providers list hands out the provider KEY id as the
 * connection id — so the id must be probed as a key, not as a provider slug.
 * The old handler called `/v1/me/providers/{id}/probe`, which answered
 * `404 No provider '19'.` for every connection.
 *
 * The HTTP route is now the bridge's job alone. This module keeps only the
 * in-process entry point, pointed at the endpoint the bridge uses.
 */

import { callGateway } from "@/bridge/gateway";

export type ConnectionTestResult = {
  /** The scheduler branches on this; it must not be named `success`. */
  valid: boolean;
  connectionId: string;
  error?: string;
  details?: unknown;
};

/**
 * Re-probe one stored provider key.
 *
 * `connectionId` is a provider KEY id — the same value the providers list
 * exposes as `connection.id`.
 */
export async function testSingleConnection(connectionId: string): Promise<ConnectionTestResult> {
  const result = await callGateway(`/v1/me/provider-keys/${connectionId}/probe`, {
    method: "POST",
  });
  if (!result.ok) return { valid: false, connectionId, error: result.error };
  return { valid: true, connectionId, details: result.data };
}
