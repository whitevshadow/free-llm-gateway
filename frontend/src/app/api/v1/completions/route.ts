/** Legacy OpenAI completions. The gateway exposes chat completions only, so the
 *  call is forwarded there; clients using the legacy shape get the gateway's own
 *  error rather than a 404 from Next. */
import { passthrough } from "@/bridge/passthrough";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  return passthrough(request, "/v1/chat/completions");
}
