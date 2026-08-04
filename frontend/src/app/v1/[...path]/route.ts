/**
 * `/v1/*` on the dashboard origin — a passthrough to the gateway.
 *
 * Several ported screens call the OpenAI-compatible API at the SITE root rather
 * than under `/api` (the Endpoints page does `fetch("/v1/models")`), because in
 * OmniRoute the dashboard and the gateway were one server on one port. Here they
 * are two containers, so those calls landed on the Next server and 404'd — which
 * is why the Endpoints page reported "0 models across 3 endpoints" while the
 * gateway was serving 86.
 *
 * Everything is forwarded verbatim, including the body and the streaming
 * response, so this doubles as a working `/v1` for anything pointed at the
 * dashboard's port by mistake. The gateway remains the real endpoint; this is a
 * convenience, not a second front door — it carries the caller's session or
 * bearer token through unchanged and grants nothing on its own.
 */

import { GATEWAY_URL } from "@/bridge/gateway";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function forward(request: Request, path: string[]): Promise<Response> {
  const url = new URL(request.url);
  const target = `${GATEWAY_URL}/v1/${path.join("/")}${url.search}`;

  const headers: Record<string, string> = {};
  const contentType = request.headers.get("content-type");
  const accept = request.headers.get("accept");
  const cookie = request.headers.get("cookie");
  const authorization = request.headers.get("authorization");
  const apiKey = request.headers.get("x-api-key");
  if (contentType) headers["content-type"] = contentType;
  if (accept) headers["accept"] = accept;
  if (cookie) headers.cookie = cookie;
  if (authorization) headers.authorization = authorization;
  if (apiKey) headers["x-api-key"] = apiKey;

  const method = request.method.toUpperCase();
  const body =
    method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(target, { method, headers, body, cache: "no-store" });
  } catch (error) {
    return Response.json(
      { error: { message: `Cannot reach the gateway: ${(error as Error).message}` } },
      { status: 502 }
    );
  }

  // Piped, not buffered — /v1/chat/completions streams.
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") || "application/json",
      "cache-control": "no-store",
    },
  });
}

export async function GET(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}
export async function POST(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}
export async function PUT(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}
export async function PATCH(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}
export async function DELETE(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}
