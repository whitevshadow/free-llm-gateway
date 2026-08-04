/**
 * OpenAI-compatible embeddings, proxied to the FastAPI gateway.
 *
 * `handleValidatedEmbeddingRequestBody` is exported because src/lib's model-test
 * runner calls it in-process to probe an embedding model without an HTTP hop.
 */

import { GATEWAY_URL } from "@/bridge/gateway";

export const dynamic = "force-dynamic";

async function forward(body: string, headers: Record<string, string>): Promise<Response> {
  try {
    const upstream = await fetch(`${GATEWAY_URL}/v1/embeddings`, {
      method: "POST",
      headers: { "content-type": "application/json", ...headers },
      body,
      cache: "no-store",
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  } catch (error) {
    return Response.json(
      { error: { message: `Cannot reach the gateway: ${(error as Error).message}` } },
      { status: 502 }
    );
  }
}

/** In-process entry point used by the model-test runner. */
export async function handleValidatedEmbeddingRequestBody(body: unknown): Promise<Response> {
  return forward(typeof body === "string" ? body : JSON.stringify(body), {});
}

export async function POST(request: Request): Promise<Response> {
  const headers: Record<string, string> = {};
  const cookie = request.headers.get("cookie");
  const authorization = request.headers.get("authorization");
  if (cookie) headers.cookie = cookie;
  if (authorization) headers.authorization = authorization;
  return forward(await request.text(), headers);
}
