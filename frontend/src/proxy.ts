/**
 * Next.js middleware — the dashboard's front door.
 *
 * WHAT THIS REPLACED
 *   OmniRoute ran a full authz pipeline here (runAuthzPipeline in
 *   server/authz/pipeline.ts): route classification, local-only path guards, IP
 *   filtering, management-password checks and rate limiting — all reading its
 *   local SQLite database. That database does not exist in this deployment, so
 *   the pipeline throws on import and every matched request 500s.
 *
 * WHAT IT DOES NOW
 *   One job: keep logged-out browsers out of the dashboard shell. Presence of
 *   the session cookie is the only thing checked, and deliberately so — this is
 *   a NAVIGATION guard, not an authorization boundary. The cookie's signature is
 *   verified by the gateway on every request the bridge forwards
 *   (backend/app/api/session_auth.py), which is where authorization actually
 *   happens. Forging a cookie value here buys you a rendered shell whose every
 *   data call then 401s.
 *
 *   `/api/*` is deliberately NOT matched: those requests must reach the bridge
 *   so it can return a real status from the gateway. Redirecting an XHR to an
 *   HTML login page is how you get "Unexpected token < in JSON" instead of a
 *   usable error.
 */

import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIE = "auth_token";

/** Paths that must stay reachable while logged out. */
const PUBLIC_PATHS = new Set([
  "/login",
  "/landing",
  "/healthz",
  "/privacy",
  "/terms",
  "/offline",
  "/maintenance",
  "/forgot-password",
]);

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (PUBLIC_PATHS.has(pathname)) return NextResponse.next();
  if (request.cookies.has(SESSION_COOKIE)) return NextResponse.next();

  const login = request.nextUrl.clone();
  login.pathname = "/login";
  // Preserve where they were headed so login can send them back.
  login.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname)}`;
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/", "/dashboard/:path*", "/home", "/home/:path*"],
};
