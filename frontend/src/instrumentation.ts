/**
 * Next.js instrumentation hook — runs once at server start.
 *
 * WHAT THIS USED TO DO
 *   OmniRoute booted its entire Node backend from here: opening the local SQLite
 *   database, running migrations, starting the credential-health scheduler, the
 *   MITM proxy manager, the model-sync jobs and the WebSocket server. That
 *   backend is gone — this deployment's backend is the FastAPI gateway — so the
 *   original hook throws on its first import and takes the server down with it
 *   ("An error occurred while loading instrumentation hook", then a 500 on every
 *   route including static ones).
 *
 * WHAT IT DOES NOW
 *   Marks the lifecycle phase and nothing else. The dashboard is stateless: it
 *   renders pages and its `/api/*` bridge forwards to the gateway. There is
 *   nothing local to warm up, and anything that genuinely needs starting belongs
 *   in the gateway, which has its own lifecycle.
 *
 *   instrumentation-node.ts is left in place for reference but is no longer
 *   imported by anything.
 */

import { markServerReady, markServerStarting } from "@/lib/serverLifecycle";

export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  markServerStarting();

  // Parts of the UI gate their first render on the lifecycle phase, so "ready"
  // must still be reached — there is simply no async startup work to await
  // before it.
  markServerReady();
}
