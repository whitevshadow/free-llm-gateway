/** Image generation — implemented by the gateway (api/openai_compat.py). */
import { passthrough } from "@/bridge/passthrough";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  return passthrough(request, "/v1/images/generations");
}
