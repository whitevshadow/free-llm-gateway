/** Not implemented by this gateway — see OMNIROUTE_INTEGRATION.md. The route
 *  exists so the dashboard receives the standard not-wired envelope. */
import { notImplementedBody } from "@/bridge/notImplemented";

export const dynamic = "force-dynamic";

export async function POST(): Promise<Response> {
  return Response.json(notImplementedBody(["v1", "responses"], "POST"), { status: 501 });
}
