/**
 * Verbatim proxy to the FastAPI gateway for OpenAI-compatible endpoints.
 *
 * These paths are not reshaped: the dashboard (and anything else pointed at
 * `/api/v1/*`) speaks the OpenAI wire format, and so does the gateway, so the
 * bridge's job here is transport only. Streaming bodies are piped, never
 * buffered.
 */

import { GATEWAY_URL } from "./gateway";

export async function passthrough(request: Request, gatewayPath: string): Promise<Response> {
  const headers: Record<string, string> = {};
  const contentType = request.headers.get("content-type");
  const cookie = request.headers.get("cookie");
  const authorization = request.headers.get("authorization");
  if (contentType) headers["content-type"] = contentType;
  if (cookie) headers.cookie = cookie;
  if (authorization) headers.authorization = authorization;

  const method = request.method.toUpperCase();
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(`${GATEWAY_URL}${gatewayPath}`, {
      method,
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

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") || "application/json",
      "cache-control": "no-store",
    },
  });
}
