/**
 * Send the browser back to /login when the dashboard session dies.
 *
 * WHY THIS EXISTS
 *   proxy.ts guards navigation on the PRESENCE of the auth_token cookie, not its
 *   validity — deliberately, since only the gateway can verify the signature. So
 *   a session that expired while a tab sat open still renders the whole shell,
 *   and every data call underneath it 401s. Each page then interprets that 401
 *   in its own local terms: the add-key modal showed "Invalid — Missing API key"
 *   against a provider key that was never actually tested.
 *
 *   Fixing that per page would mean touching ~190 of them. The bridge already
 *   funnels every /api/* call through one place and now tags auth failures with
 *   `sessionExpired` (see bridge/routes.ts), so one fetch wrapper here catches
 *   all of them.
 *
 * WHY A FETCH WRAPPER
 *   It mirrors installDashboardCsrfFetch, which the same layout already installs
 *   for the same reason — the pages call bare fetch(), and this is the only
 *   choke point that covers them without a rewrite.
 */

/** Auth endpoints answer 401 to mean "wrong credential", not "session gone". */
const AUTH_PATHS = ["/api/auth/login", "/api/auth/logout", "/api/auth/csrf", "/api/auth/register"];

let originalFetch: typeof fetch | null = null;
let installCount = 0;
/** Concurrent page loads produce a burst of 401s; only the first may navigate. */
let redirecting = false;

export function __resetSessionExpiryForTests(): void {
  if (originalFetch) {
    globalThis.fetch = originalFetch;
    originalFetch = null;
  }
  installCount = 0;
  redirecting = false;
}

function isDashboardApiPath(input: RequestInfo | URL): boolean {
  if (typeof window === "undefined") return false;

  const raw =
    typeof Request !== "undefined" && input instanceof Request
      ? input.url
      : input instanceof URL
        ? input.href
        : typeof input === "string"
          ? input
          : null;
  if (!raw) return false;

  let url: URL;
  try {
    url = new URL(raw, window.location.href);
  } catch {
    return false;
  }

  if (url.origin !== window.location.origin) return false;
  if (!url.pathname.startsWith("/api/")) return false;
  return !AUTH_PATHS.includes(url.pathname);
}

function redirectToLogin(): void {
  if (redirecting) return;
  redirecting = true;

  const { pathname, search } = window.location;
  // Already on the way out — don't bounce the login page onto itself.
  if (pathname === "/login") return;

  const next = encodeURIComponent(`${pathname}${search}`);
  // A full assignment rather than router.push: the stale session left React
  // state all over the tree, and every one of those pages is showing data it is
  // no longer entitled to. A document load is the only reliable way to drop it.
  window.location.assign(`/login?next=${next}`);
}

/**
 * Returns true when the response is the bridge's session-expired envelope.
 * The body is read from a clone so the caller still gets an unconsumed stream.
 */
async function isSessionExpired(response: Response): Promise<boolean> {
  if (response.status !== 401) return false;
  try {
    const body = (await response.clone().json()) as { sessionExpired?: unknown } | null;
    return body?.sessionExpired === true;
  } catch {
    // A 401 the bridge did not shape (e.g. an edge/proxy rejection) is still an
    // auth failure — treat it as one rather than stranding the user in a shell
    // whose every call fails.
    return true;
  }
}

export function installSessionExpiryFetch(): () => void {
  if (typeof globalThis.fetch !== "function") return () => {};

  if (installCount === 0) {
    originalFetch = globalThis.fetch;

    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const call = originalFetch ?? fetch;
      const response = await call(input, init);

      if (response.status === 401 && isDashboardApiPath(input)) {
        if (await isSessionExpired(response)) redirectToLogin();
      }

      return response;
    }) as typeof fetch;
  }

  installCount++;
  let active = true;

  return () => {
    if (!active) return;
    active = false;
    installCount = Math.max(0, installCount - 1);
    if (installCount === 0 && originalFetch) {
      globalThis.fetch = originalFetch;
      originalFetch = null;
    }
  };
}
