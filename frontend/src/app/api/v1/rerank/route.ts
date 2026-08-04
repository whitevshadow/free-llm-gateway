/**
 * Reranking is not part of this gateway.
 *
 * The endpoint exists so the dashboard gets a clear 501 rather than a 404 that
 * reads as a routing bug, and so src/lib's model-test runner — which imports
 * POST directly — still resolves.
 */

import { notImplementedBody } from "@/bridge/notImplemented";

export const dynamic = "force-dynamic";

export async function POST(): Promise<Response> {
  return Response.json(notImplementedBody(["v1", "rerank"], "POST"), { status: 501 });
}
