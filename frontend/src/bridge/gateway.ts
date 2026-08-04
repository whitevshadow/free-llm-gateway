/**
 * Talking to the FastAPI gateway.
 *
 * The dashboard UI was lifted from OmniRoute, where every `/api/*` path was
 * served by a Next route handler backed by a local Node/SQLite stack. That
 * backend is gone: this project's backend is the FastAPI gateway in ../backend.
 *
 * Nothing here is exposed to the browser. The bridge route handler
 * (src/app/api/[...path]/route.ts) runs server-side, so GATEWAY_URL may point at
 * a container-internal host that the browser cannot reach.
 */

export const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");

/** Cookie the gateway issues on login. Mirrors OmniRoute's own cookie name so
 *  the ported login page and its `authenticated` checks work unchanged. */
export const SESSION_COOKIE = "auth_token";

export type GatewayResult<T> =
  | { ok: true; status: number; data: T }
  | {
      ok: false;
      status: number;
      error: string;
      /** The gateway's own parsed body, when it sent one. Preserved because
       *  several endpoints answer a failure with structure worth showing — the
       *  SRS §4/§5 gap stubs return the section, the reason and what to use
       *  instead, all of which is lost if only `error` survives. */
      body?: unknown;
    };

/**
 * Call the gateway, forwarding the caller's session so FastAPI can resolve the
 * user. Both the cookie and an explicit bearer token are forwarded: the cookie
 * covers the dashboard, the bearer covers anyone driving the bridge with a raw
 * gateway key.
 */
export async function callGateway<T = unknown>(
  path: string,
  init: {
    method?: string;
    body?: BodyInit | null;
    headers?: Record<string, string>;
    cookie?: string | null;
    authorization?: string | null;
    search?: string;
    signal?: AbortSignal;
  } = {}
): Promise<GatewayResult<T>> {
  const url = `${GATEWAY_URL}${path}${init.search ? `?${init.search}` : ""}`;

  const headers: Record<string, string> = { ...(init.headers || {}) };
  if (init.cookie) headers["cookie"] = init.cookie;
  if (init.authorization) headers["authorization"] = init.authorization;

  let response: Response;
  try {
    response = await fetch(url, {
      method: init.method || "GET",
      headers,
      body: init.body ?? undefined,
      // The gateway is the source of truth; a cached dashboard read would show
      // stale key/quota state right after a mutation.
      cache: "no-store",
      signal: init.signal,
    });
  } catch (error) {
    // A connection refusal is the single most common failure here (backend not
    // started yet), and it must not surface as an opaque 500.
    return {
      ok: false,
      status: 502,
      error: `Cannot reach the gateway at ${GATEWAY_URL}: ${(error as Error).message}`,
    };
  }

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }

  if (!response.ok) {
    const detail =
      parsed && typeof parsed === "object" && "detail" in (parsed as Record<string, unknown>)
        ? String((parsed as Record<string, unknown>).detail)
        : typeof parsed === "string" && parsed
          ? parsed
          : response.statusText;
    return { ok: false, status: response.status, error: detail, body: parsed };
  }

  return { ok: true, status: response.status, data: parsed as T };
}

/** Read the raw Cookie header value for the gateway session, if present. */
export function sessionCookieHeader(cookieHeader: string | null): string | null {
  if (!cookieHeader) return null;
  return cookieHeader.includes(`${SESSION_COOKIE}=`) ? cookieHeader : cookieHeader;
}
