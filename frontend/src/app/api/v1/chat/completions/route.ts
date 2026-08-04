/**
 * OpenAI-compatible chat completions, proxied to the FastAPI gateway.
 *
 * The dashboard's Playground posts here, and several orphaned `src/lib` modules
 * (evals, chaos, model tests) import `POST` directly to run a completion
 * in-process — which is why this exists as a real route rather than living only
 * in the bridge table.
 *
 * Streaming is passed through untouched: the response body is piped straight to
 * the caller so SSE tokens arrive as the gateway emits them. Buffering here
 * would defeat the entire point of a streaming endpoint.
 */

import { GATEWAY_URL } from "@/bridge/gateway";

export const dynamic = "force-dynamic";
// Streaming responses must not be collected by the Node runtime's buffering.
export const runtime = "nodejs";

export async function POST(request: Request): Promise<Response> {
  const body = await request.text();

  const headers: Record<string, string> = { "content-type": "application/json" };
  const cookie = request.headers.get("cookie");
  const authorization = request.headers.get("authorization");
  if (cookie) headers.cookie = cookie;
  if (authorization) headers.authorization = authorization;

  let upstream: Response;
  try {
    upstream = await fetch(`${GATEWAY_URL}/v1/chat/completions`, {
      method: "POST",
      headers,
      body,
      cache: "no-store",
    });
  } catch (error) {
    return Response.json(
      { error: { message: `Cannot reach the gateway: ${(error as Error).message}` } },
      { status: 502 }
    );
  }

  // Pipe the body through verbatim, preserving the upstream content-type so an
  // SSE stream stays an SSE stream.
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") || "application/json",
      "cache-control": "no-store",
    },
  });
}
