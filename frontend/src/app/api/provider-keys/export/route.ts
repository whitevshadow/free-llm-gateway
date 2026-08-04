/**
 * CSV backup of the caller's provider keys.
 *
 * A dedicated route rather than a bridge-table entry because the response is
 * text/csv with a Content-Disposition attachment header — the bridge parses
 * everything as JSON and would turn this into a quoted string with the download
 * headers stripped.
 *
 * This is the ONE endpoint that returns provider secrets in plaintext (see the
 * docstring on export_my_provider_keys in backend/app/api/admin.py): a user
 * restoring after a lost database needs the original keys back, and they already
 * own them. Everything is forwarded verbatim, including `Cache-Control: no-store`
 * from the gateway, so no proxy or browser cache holds the plaintext.
 */

import { GATEWAY_URL } from "@/bridge/gateway";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  const headers: Record<string, string> = {};
  const cookie = request.headers.get("cookie");
  const authorization = request.headers.get("authorization");
  if (cookie) headers.cookie = cookie;
  if (authorization) headers.authorization = authorization;

  let upstream: Response;
  try {
    upstream = await fetch(`${GATEWAY_URL}/v1/me/provider-keys/export`, {
      headers,
      cache: "no-store",
    });
  } catch (error) {
    return Response.json(
      { error: `Cannot reach the gateway: ${(error as Error).message}` },
      { status: 502 }
    );
  }

  // The gateway answers 404 when there is nothing to export. Pass its JSON
  // through rather than handing the browser an empty file download.
  if (!upstream.ok) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": upstream.headers.get("content-type") || "text/csv",
      "content-disposition":
        upstream.headers.get("content-disposition") || 'attachment; filename="provider-keys.csv"',
      "cache-control": "no-store",
    },
  });
}
