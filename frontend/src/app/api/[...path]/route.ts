/**
 * The bridge: every `/api/*` call the dashboard makes lands here.
 *
 * OmniRoute served these paths from ~600 local route handlers backed by its own
 * Node/SQLite stack. Those were removed — this project's backend is the FastAPI
 * gateway. This single catch-all translates the UI's calls into gateway calls
 * (see ../../../bridge/routes.ts) and answers everything else with an explicit
 * 501 (see ../../../bridge/notImplemented.ts).
 *
 * A catch-all rather than ~200 individual files is deliberate: the mapping is
 * data, and keeping it in one table makes "what is actually wired?" a question
 * you can answer by reading a single file.
 */

import { NextResponse } from "next/server";

import { matchRoute, type BridgeContext } from "@/bridge/routes";
import { notImplementedBody } from "@/bridge/notImplemented";

// Session-dependent and never cacheable: responses are per-user.
export const dynamic = "force-dynamic";

async function handle(request: Request, params: Promise<{ path: string[] }>) {
  const { path } = await params;
  const segments = Array.isArray(path) ? path : [];
  const method = request.method.toUpperCase();
  const url = new URL(request.url);

  // Read the body once — it cannot be read again downstream.
  let body: string | null = null;
  if (method !== "GET" && method !== "HEAD" && method !== "DELETE") {
    try {
      body = await request.text();
    } catch {
      body = null;
    }
  }

  const ctx: BridgeContext = {
    segments,
    method,
    search: url.searchParams.toString(),
    cookie: request.headers.get("cookie"),
    authorization: request.headers.get("authorization"),
    body: body || null,
  };

  const matched = matchRoute(segments, method);
  if (!matched) {
    return NextResponse.json(notImplementedBody(segments, method), {
      status: 501,
      headers: { "cache-control": "no-store" },
    });
  }

  try {
    const result = await matched.route.handle(ctx, matched.params);
    const response = NextResponse.json(result.body, { status: result.status });
    response.headers.set("cache-control", "no-store");
    for (const [key, value] of Object.entries(result.headers || {})) {
      // set-cookie must be appended, not set, so a multi-cookie response from
      // the gateway is not collapsed into one header.
      if (key.toLowerCase() === "set-cookie") response.headers.append("set-cookie", value);
      else response.headers.set(key, value);
    }
    return response;
  } catch (error) {
    // An adapter throwing is a bug in the bridge, not a gateway failure — say so
    // rather than blaming the backend.
    return NextResponse.json(
      {
        error: `Bridge adapter failed for /api/${segments.join("/")}: ${(error as Error).message}`,
        code: "bridge_adapter_error",
      },
      { status: 500 }
    );
  }
}

export async function GET(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return handle(request, ctx.params);
}
export async function POST(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return handle(request, ctx.params);
}
export async function PUT(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return handle(request, ctx.params);
}
export async function PATCH(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return handle(request, ctx.params);
}
export async function DELETE(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return handle(request, ctx.params);
}
