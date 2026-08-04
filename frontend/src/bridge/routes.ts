/**
 * The bridge routing table: OmniRoute UI path -> FastAPI gateway endpoint.
 *
 * The dashboard calls ~210 distinct `/api/*` paths. This gateway currently
 * implements the subset below; everything else is answered with a 501 and a
 * machine-readable `notImplemented` marker (see notImplemented.ts) so pages
 * degrade to an explicit "not wired yet" state instead of throwing.
 *
 * ADDING A ROUTE
 *   1. Implement the endpoint in ../backend.
 *   2. Add an entry here, with an `adapt` that reshapes the FastAPI response
 *      into the contract the OmniRoute page already expects.
 *   3. Delete the corresponding line from NOT_WIRED in notImplemented.ts.
 *
 * The `adapt` step is not optional bureaucracy: the UI was written against
 * OmniRoute's own JSON shapes, and a page that receives the right data under
 * the wrong key renders empty with no error.
 */

import { callGateway, type GatewayResult } from "./gateway";
import { toCatalogId, toGatewaySlug } from "./providerSlug";

export type BridgeContext = {
  /** Path segments after `/api/`, e.g. ["providers"] or ["keys", "3"]. */
  segments: string[];
  method: string;
  search: string;
  cookie: string | null;
  authorization: string | null;
  /** Raw request body, already read. Null for GET/DELETE. */
  body: string | null;
};

export type BridgeRoute = {
  /** Matches ctx.segments. A segment of `:param` matches any single value. */
  pattern: string;
  method: string;
  /** Build the call to FastAPI and reshape the result. */
  handle: (ctx: BridgeContext, params: Record<string, string>) => Promise<BridgeResponse>;
};

export type BridgeResponse = {
  status: number;
  body: unknown;
  /** Forwarded verbatim — used to relay the gateway's Set-Cookie on login. */
  headers?: Record<string, string>;
};

/**
 * Turn a failed gateway call into the error envelope the UI understands.
 *
 * When the gateway sent a structured body it is passed through whole — the
 * SRS §4/§5 gap stubs answer a 501 with the section, the reason and what to use
 * instead, and flattening that to a single string throws away the only part
 * worth reading.
 */
function fail(result: Extract<GatewayResult<unknown>, { ok: false }>): BridgeResponse {
  // A 401 means the dashboard session is gone or expired — it says nothing about
  // the data the page asked for. Left unmarked, the gateway's "Missing API key."
  // reaches domain UI that reads any failure as a verdict on its own input: the
  // add-key modal rendered it as "Invalid — Missing API key", i.e. it blamed the
  // provider key the user had just typed for an auth failure of the dashboard
  // itself. The marker below is what installSessionExpiryFetch watches for.
  if (result.status === 401) {
    return {
      status: 401,
      body: {
        error: "Your dashboard session has expired. Sign in again.",
        sessionExpired: true,
        code: "session_expired",
        gatewayError: result.error,
      },
    };
  }
  if (result.body && typeof result.body === "object") {
    return { status: result.status, body: { error: result.error, ...result.body } };
  }
  return { status: result.status, body: { error: result.error } };
}

/** Shorthand: forward the request unchanged and reshape the success payload. */
function proxy<T>(
  path: string,
  adapt: (data: T, ctx: BridgeContext) => unknown
): BridgeRoute["handle"] {
  return async (ctx) => {
    const result = await callGateway<T>(path, {
      method: ctx.method,
      search: ctx.search,
      cookie: ctx.cookie,
      authorization: ctx.authorization,
      body: ctx.body,
      headers: ctx.body ? { "content-type": "application/json" } : {},
    });
    if (!result.ok) return fail(result);
    return { status: result.status, body: adapt(result.data, ctx) };
  };
}

// ── FastAPI response shapes we consume ──────────────────────────────────────

type FastApiProviders = {
  providers: Array<{
    id: number;
    slug: string;
    name: string;
    base_url: string;
    docs_url: string | null;
    key_hint: string | null;
    model_count: number;
    has_my_key: boolean;
  }>;
};

type FastApiProviderKeys = {
  keys: Array<{
    id: number;
    provider: string;
    label: string | null;
    masked: string;
    is_active: boolean;
    working_models: number;
    total_models: number;
  }>;
};

type FastApiMyModels = {
  models: Array<{
    model: string;
    mode: string | null;
    publisher: string | null;
    providers: string[] | string | null;
    is_usable: boolean;
    has_backup_key: boolean;
    has_backup_provider: boolean;
    live_keys: number;
    total_keys: number;
    statuses: string[] | string | null;
    last_checked_at: string | null;
  }>;
};

type FastApiUsage = {
  window_days: number;
  totals: {
    requests: number;
    total_tokens: number;
    prompt_tokens: number;
    completion_tokens: number;
    cost: number;
    avg_latency_ms: number;
    errors: number;
  };
  top_models: Array<{ model: string; tokens: number; requests: number }>;
  top_providers: Array<{ provider: string; slug: string; tokens: number; requests: number }>;
  daily: Array<{ date: string; tokens: number; requests: number }>;
  per_key: Array<{
    id: number;
    label: string | null;
    masked: string;
    provider: string;
    requests: number;
    tokens: number;
    working_models: number;
  }>;
};

type FastApiLogs = {
  total: number;
  limit: number;
  offset: number;
  logs: Array<Record<string, unknown>>;
};

function asArray(value: string[] | string | null | undefined): string[] {
  if (Array.isArray(value)) return value;
  if (typeof value === "string" && value) return value.split(",").map((s) => s.trim());
  return [];
}

// ── The table ───────────────────────────────────────────────────────────────

export const ROUTES: BridgeRoute[] = [
  // ── auth ──────────────────────────────────────────────────────────────────
  // The UI posts { password }. FastAPI accepts a gateway key (or the master
  // admin key) as that password and replies with a Set-Cookie session, which is
  // relayed to the browser untouched so the cookie is set on the dashboard origin.
  {
    pattern: "auth/login",
    method: "POST",
    handle: async (ctx) => {
      const result = await callGatewayWithHeaders("/v1/auth/login", ctx);
      return result;
    },
  },
  {
    pattern: "auth/logout",
    method: "POST",
    handle: async (ctx) => callGatewayWithHeaders("/v1/auth/logout", ctx),
  },
  {
    pattern: "auth/status",
    method: "GET",
    handle: proxy<{ authenticated: boolean }>("/v1/auth/status", (data) => data),
  },
  {
    pattern: "auth/csrf",
    method: "GET",
    // The gateway uses a SameSite=Lax session cookie and no form posts, so there
    // is no CSRF token to mint. The UI only needs the call to succeed.
    handle: async () => ({ status: 200, body: { csrfToken: null } }),
  },

  // ── liveness ──────────────────────────────────────────────────────────────
  {
    pattern: "health/ping",
    method: "GET",
    handle: async (ctx) => {
      const startedAt = Date.now();
      const result = await callGateway<Record<string, unknown>>("/health", {
        cookie: ctx.cookie,
        authorization: ctx.authorization,
      });
      if (!result.ok) return { status: 503, body: { status: "error", error: result.error } };
      return {
        status: 200,
        body: {
          status: "ok",
          timestamp: new Date().toISOString(),
          latencyMs: Date.now() - startedAt,
          gateway: result.data,
        },
      };
    },
  },

  // ── identity ──────────────────────────────────────────────────────────────
  {
    pattern: "me",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me", (data) => data),
  },

  // ── providers ─────────────────────────────────────────────────────────────
  // OmniRoute calls a configured provider a "connection" and keys it by string
  // id; FastAPI models it as a provider row plus a separate key. One provider
  // maps to one connection here, marked active when the user holds a key for it.
  {
    pattern: "providers",
    method: "GET",
    handle: async (ctx) => {
      // A "connection" in OmniRoute is ONE CREDENTIAL, not one provider. Two
      // NVIDIA keys are two connections — which is the whole point of this
      // gateway (SRS §6.1: two keys are two independent free-tier budgets).
      // Collapsing them to one row per provider made a provider with two live
      // keys render "0 connections", because the page counts rows.
      const [keysResult, providersResult] = await Promise.all([
        callGateway<FastApiProviderKeys>("/v1/me/provider-keys", {
          cookie: ctx.cookie,
          authorization: ctx.authorization,
        }),
        callGateway<FastApiProviders>("/v1/providers", {
          cookie: ctx.cookie,
          authorization: ctx.authorization,
        }),
      ]);
      if (!keysResult.ok) return fail(keysResult);

      const providers = providersResult.ok ? providersResult.data.providers : [];
      const meta = new Map(providers.map((p) => [p.slug, p]));

      const connections = keysResult.data.keys.map((k) => {
        const p = meta.get(k.provider);

        // The provider CARD counts a connection only when three things line up
        // (see providers/providerPageUtils.ts + shared/utils/providerConnectionStatus.ts):
        //   authType === the card's section ("apikey" here),
        //   isActive !== false,
        //   testStatus in {active, success, unknown}.
        // Omitting authType made every card read "No connections" while the
        // data was all present — the rows simply never matched.
        //
        // testStatus is derived from what the gateway actually knows: a key with
        // zero working models is not "connected", it is a key whose every model
        // failed its probe.
        const testStatus =
          k.total_models > 0 && k.working_models === 0 ? "error" : "active";

        return {
          // The connection id is the KEY id — stable, and what per-connection
          // actions (test, edit, delete) address.
          id: String(k.id),
          // The CATALOG id, because the page routes and filters on that.
          provider: toCatalogId(k.provider),
          // The gateway's own slug, for anything that needs to call back.
          providerSlug: k.provider,
          authType: "apikey",
          testStatus,
          name: k.label || p?.name || k.provider,
          apiKey: k.masked,
          isActive: k.is_active,
          baseUrl: p?.base_url ?? null,
          docsUrl: p?.docs_url ?? null,
          keyHint: p?.key_hint ?? null,
          modelCount: p?.model_count ?? 0,
          workingModels: k.working_models,
          totalModels: k.total_models,
        };
      });

      return { status: 200, body: { connections, total: connections.length } };
    },
  },

  // ── provider keys ─────────────────────────────────────────────────────────
  {
    pattern: "keys",
    method: "GET",
    handle: proxy<FastApiProviderKeys>("/v1/me/provider-keys", (data) => ({
      keys: data.keys.map((k) => ({
        id: String(k.id),
        key: k.masked,
        name: k.label || k.provider,
        provider: k.provider,
        isActive: k.is_active,
        workingModels: k.working_models,
        totalModels: k.total_models,
      })),
      total: data.keys.length,
      // FastAPI never returns a stored secret through the JSON API; the CSV
      // export is the only reveal path, so the UI's reveal affordance stays off.
      allowKeyReveal: false,
    })),
  },
  {
    pattern: "keys",
    method: "POST",
    handle: proxy<Record<string, unknown>>("/v1/me/provider-keys", (data) => data),
  },
  {
    pattern: "keys/:id",
    method: "DELETE",
    handle: async (ctx, params) => {
      const result = await callGateway(`/v1/me/provider-keys/${params.id}`, {
        method: "DELETE",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
      });
      if (!result.ok) return fail(result);
      return { status: 200, body: { success: true } };
    },
  },

  // ── models ────────────────────────────────────────────────────────────────
  {
    pattern: "models",
    method: "GET",
    handle: proxy<FastApiMyModels>("/v1/me/models", (data) => ({
      models: data.models.map((m) => {
        const providers = asArray(m.providers);
        const provider = providers[0] || "gateway";
        return {
          provider,
          model: m.model,
          name: m.model,
          fullModel: `${provider}/${m.model}`,
          alias: m.model,
          available: m.is_usable,
          mode: m.mode,
          publisher: m.publisher,
          providers,
          statuses: asArray(m.statuses),
          liveKeys: m.live_keys,
          totalKeys: m.total_keys,
          hasBackupKey: m.has_backup_key,
          hasBackupProvider: m.has_backup_provider,
          lastCheckedAt: m.last_checked_at,
        };
      }),
    })),
  },

  // The OpenAI-shaped model list, under the OpenAI path. Distinct from `models`
  // above: that one reshapes into OmniRoute's registry contract, this one is the
  // verbatim `{ object, data: [{ id, ... }] }` envelope. Model pickers that were
  // written against a plain OpenAI endpoint (the translator's model list, the
  // agent-bridge selector) read `data`, and answering them with the registry
  // shape leaves them empty with no error.
  {
    pattern: "v1/models",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/models", (data) => data),
  },

  // ── usage / analytics ─────────────────────────────────────────────────────
  {
    pattern: "usage",
    method: "GET",
    handle: proxy<FastApiUsage>("/v1/me/usage", (data) => ({
      windowDays: data.window_days,
      totals: {
        requests: data.totals.requests,
        totalTokens: data.totals.total_tokens,
        promptTokens: data.totals.prompt_tokens,
        completionTokens: data.totals.completion_tokens,
        cost: data.totals.cost,
        avgLatencyMs: data.totals.avg_latency_ms,
        errors: data.totals.errors,
      },
      topModels: data.top_models,
      topProviders: data.top_providers,
      daily: data.daily,
      perKey: data.per_key,
    })),
  },

  // ── request log ───────────────────────────────────────────────────────────
  {
    pattern: "logs",
    method: "GET",
    handle: proxy<FastApiLogs>("/v1/me/logs", (data) => ({
      logs: data.logs,
      total: data.total,
      limit: data.limit,
      offset: data.offset,
    })),
  },

  // ── settings ──────────────────────────────────────────────────────────────
  // The single busiest path on the providers page (15 call sites). Backed by a
  // per-user key/value store, with the routing settings merged in on read and
  // split back out on write — see backend/app/api/settings.py for why routing
  // must stay typed rather than living in the same blob.
  {
    pattern: "settings",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/settings", (data) => data),
  },
  {
    pattern: "settings",
    method: "PUT",
    handle: proxy<Record<string, unknown>>("/v1/me/settings", (data) => data),
  },
  {
    pattern: "settings",
    method: "POST",
    // Some screens POST rather than PUT for the same partial-merge semantics.
    handle: proxy<Record<string, unknown>>("/v1/me/settings", (data) => data),
  },

  // ── per-provider parameter filters ────────────────────────────────────────
  // Providers 400 on parameters they do not implement (NVIDIA NIM rejects
  // `thinking`), and a 400 is not retryable — so one unsupported field fails a
  // request any sibling deployment could have served.
  {
    pattern: "providers/:id/param-filters",
    method: "GET",
    handle: async (ctx, params) => {
      const result = await callGateway<Record<string, unknown>>(
        `/v1/me/providers/${toGatewaySlug(params.id)}/param-filters`,
        { cookie: ctx.cookie, authorization: ctx.authorization }
      );
      if (!result.ok) return fail(result);
      const d = result.data as Record<string, unknown>;
      return {
        status: 200,
        body: {
          blockedParams: d.blocked ?? [],
          allowedParams: d.allowed ?? [],
          autoLearn: d.auto_learn ?? false,
        },
      };
    },
  },
  {
    pattern: "providers/:id/param-filters",
    method: "PUT",
    handle: async (ctx, params) => {
      let body: Record<string, unknown> = {};
      try {
        body = ctx.body ? (JSON.parse(ctx.body) as Record<string, unknown>) : {};
      } catch {
        return { status: 400, body: { error: "Invalid JSON body." } };
      }
      const asList = (v: unknown): string[] =>
        Array.isArray(v)
          ? v.map(String)
          : typeof v === "string"
            ? v.split(",").map((s) => s.trim()).filter(Boolean)
            : [];

      const result = await callGateway<Record<string, unknown>>(
        `/v1/me/providers/${toGatewaySlug(params.id)}/param-filters`,
        {
          method: "PUT",
          cookie: ctx.cookie,
          authorization: ctx.authorization,
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            blocked: asList(body.blockedParams ?? body.blocked),
            allowed: asList(body.allowedParams ?? body.allowed),
            auto_learn: Boolean(body.autoLearn ?? body.auto_learn),
          }),
        }
      );
      if (!result.ok) return fail(result);
      return { status: 200, body: { success: true, ...(result.data as object) } };
    },
  },

  // ── one connection ────────────────────────────────────────────────────────
  // `:id` here is a provider KEY id (see the providers GET adapter). Deleting a
  // connection deletes that credential and cascades its deployments.
  {
    pattern: "providers/:id",
    method: "DELETE",
    handle: async (ctx, params) => {
      const result = await callGateway(`/v1/me/provider-keys/${params.id}`, {
        method: "DELETE",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
      });
      if (!result.ok) return fail(result);
      return { status: 200, body: { success: true } };
    },
  },
  {
    pattern: "providers/:id/test",
    method: "POST",
    handle: async (ctx, params) => {
      const result = await callGateway(`/v1/me/provider-keys/${params.id}/probe`, {
        method: "POST",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
      });
      if (!result.ok) return fail(result);
      return { status: 200, body: { success: true, ...(result.data as object) } };
    },
  },

  // ── test every model of a provider ────────────────────────────────────────
  // Re-probes the caller's keys for that provider. Returns immediately: probing
  // 123 models inline would hang the request and rate-limit the key under test
  // (SRS §13.1). Progress is on /api/probe-status.
  {
    pattern: "models/test-all",
    method: "POST",
    handle: async (ctx) => {
      let body: Record<string, unknown> = {};
      try {
        body = ctx.body ? (JSON.parse(ctx.body) as Record<string, unknown>) : {};
      } catch {
        body = {};
      }
      const raw = String(body.provider ?? body.providerId ?? "");
      const path = raw
        ? `/v1/me/providers/${toGatewaySlug(raw)}/probe`
        : "/v1/me/probe";
      const result = await callGateway(path, {
        method: "POST",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
      });
      if (!result.ok) return fail(result);
      return {
        status: 200,
        body: {
          success: true,
          started: true,
          detail: "Probing in the background — poll /api/probe-status.",
          ...(result.data as object),
        },
      };
    },
  },

  // ── bulk provider enable/disable ──────────────────────────────────────────
  {
    pattern: "providers/bulk",
    method: "PATCH",
    handle: async (ctx) => {
      let body: Record<string, unknown> = {};
      try {
        body = ctx.body ? (JSON.parse(ctx.body) as Record<string, unknown>) : {};
      } catch {
        return { status: 400, body: { error: "Invalid JSON body." } };
      }
      const ids = Array.isArray(body.ids)
        ? body.ids
        : Array.isArray(body.providers)
          ? body.providers
          : [];
      const result = await callGateway("/v1/admin/providers-bulk", {
        method: "PATCH",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          slugs: ids.map((i) => toGatewaySlug(String(i))),
          enabled: body.enabled ?? body.isActive,
        }),
      });
      if (!result.ok) return fail(result);
      return { status: 200, body: result.data };
    },
  },

  // ── custom OpenAI/Anthropic-compatible nodes ──────────────────────────────
  // OmniRoute's "provider nodes" are user-registered compatible endpoints. Here
  // that is exactly a provider registered with a base URL and no preset, so the
  // list is derived rather than stored separately.
  {
    pattern: "provider-nodes",
    method: "GET",
    handle: proxy<FastApiProviders>("/v1/providers", (data) => ({
      nodes: data.providers.map((p) => ({
        id: p.slug,
        name: p.name,
        baseUrl: p.base_url,
        apiType: "openai",
        prefix: p.slug,
        modelCount: p.model_count,
      })),
      // The onboarding flow reads this flag before offering the CC-compatible path.
      ccCompatibleProviderEnabled: false,
    })),
  },
  {
    pattern: "provider-nodes",
    method: "POST",
    handle: proxy<Record<string, unknown>>("/v1/admin/providers", (data) => data),
  },

  // ── playground ────────────────────────────────────────────────────────────
  // The conversation itself goes through /v1/chat/completions like any other
  // client; these are the two things around it.
  {
    pattern: "playground/presets",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/playground/presets", (data) => data),
  },
  {
    pattern: "playground/presets",
    method: "POST",
    handle: proxy<Record<string, unknown>>("/v1/me/playground/presets", (data) => data),
  },
  {
    pattern: "playground/presets/:id",
    method: "DELETE",
    handle: async (ctx, params) => {
      const result = await callGateway(`/v1/me/playground/presets/${params.id}`, {
        method: "DELETE",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
      });
      if (!result.ok) return fail(result);
      return { status: 200, body: result.data };
    },
  },
  {
    pattern: "playground/improve-prompt",
    method: "POST",
    handle: proxy<Record<string, unknown>>("/v1/me/playground/improve-prompt", (data) => data),
  },

  // The Playground's model picker. Same list /v1/models serves, in the shape
  // this screen reads.
  {
    pattern: "providers/client",
    method: "GET",
    handle: proxy<FastApiMyModels>("/v1/me/models", (data) => ({
      providers: [...new Set(data.models.flatMap((m) => asArray(m.providers)))].map(
        (name) => ({ id: name, name })
      ),
      models: data.models
        .filter((m) => m.is_usable)
        .map((m) => ({
          id: m.model,
          name: m.model,
          provider: asArray(m.providers)[0] ?? "gateway",
          mode: m.mode,
        })),
    })),
  },

  // ── resilience — SRS §14, three layers kept apart ─────────────────────────
  // OmniRoute's RESILIENCE_GUIDE opens by warning not to confuse them, so these
  // report separately: provider circuits, key cooldowns, model lockouts.
  {
    pattern: "resilience",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/resilience", (data) => data),
  },
  {
    pattern: "monitoring/health",
    method: "GET",
    // The Health screen reads `system.uptime` / `system.version` on top of the
    // resilience payload. Returning resilience alone left `system` undefined and
    // the page died on the property access before it rendered anything — the
    // 501 envelope's empty-collection keys do not help here, because this route
    // IS wired; it was simply answering with a different contract than the page
    // reads. The gateway's own /health carries the uptime, so join the two.
    handle: async (ctx) => {
      const [resilience, health] = await Promise.all([
        callGateway<Record<string, unknown>>("/v1/me/resilience", {
          cookie: ctx.cookie,
          authorization: ctx.authorization,
        }),
        callGateway<{ data?: Record<string, unknown> }>("/health", {
          cookie: ctx.cookie,
          authorization: ctx.authorization,
        }),
      ]);
      if (!resilience.ok) return fail(resilience);

      const h = (health.ok ? health.data?.data : null) ?? {};
      return {
        status: 200,
        body: {
          ...resilience.data,
          system: {
            uptime: typeof h.uptime_seconds === "number" ? h.uptime_seconds : 0,
            version: typeof h.version === "string" ? h.version : "2.0.0",
            environment: h.environment ?? null,
            // The gateway is Python; there is no Node process behind these
            // numbers and inventing them would be a lie the page renders as fact.
            nodeVersion: null,
            memoryUsage: null,
          },
        },
      };
    },
  },
  {
    pattern: "resilience/reset",
    method: "POST",
    handle: async (ctx) => {
      let body: Record<string, unknown> = {};
      try {
        body = ctx.body ? (JSON.parse(ctx.body) as Record<string, unknown>) : {};
      } catch {
        body = {};
      }
      const provider = body.provider ? toGatewaySlug(String(body.provider)) : null;
      const result = await callGateway("/v1/me/resilience/reset", {
        method: "POST",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
        search: provider ? `provider=${encodeURIComponent(provider)}` : "",
      });
      if (!result.ok) return fail(result);
      return { status: 200, body: result.data };
    },
  },
  {
    pattern: "resilience/model-cooldowns",
    method: "GET",
    // The guide's model-lockout list. Derived from the resilience read so there
    // is one source for all three layers.
    handle: proxy<Record<string, unknown>>("/v1/me/resilience", (data) => ({
      lockouts: (data as Record<string, unknown>).model_lockouts ?? [],
      cooldowns: (data as Record<string, unknown>).key_cooldowns ?? [],
    })),
  },
  {
    pattern: "resilience/model-cooldowns/:id",
    method: "DELETE",
    handle: async (ctx, params) => {
      const result = await callGateway(
        `/v1/me/resilience/model-lockout/${params.id}`,
        { method: "DELETE", cookie: ctx.cookie, authorization: ctx.authorization }
      );
      if (!result.ok) return fail(result);
      return { status: 200, body: result.data };
    },
  },

  // ── what this endpoint serves ─────────────────────────────────────────────
  // Backs the Endpoints screen. `network/info` is the path that page already
  // calls; the gateway URL comes from the server-side GATEWAY_URL rather than
  // the browser's origin, because the client-facing address is the gateway's,
  // not the dashboard's.
  {
    pattern: "network/info",
    method: "GET",
    handle: async (ctx) => {
      const result = await callGateway<Record<string, unknown>>("/v1/capabilities", {
        cookie: ctx.cookie,
        authorization: ctx.authorization,
      });
      // NEXT_PUBLIC_BASE_URL is what the UI advertises; it is inlined at build
      // time and is the address clients actually reach (see frontend/Dockerfile).
      const publicUrl = (process.env.NEXT_PUBLIC_BASE_URL || "").replace(/\/+$/, "");
      const apiBase = publicUrl ? `${publicUrl}/v1` : null;
      return {
        status: 200,
        body: {
          baseUrl: publicUrl,
          apiBase,
          // `localUrl` is the key the Endpoints screen actually reads
          // (endpoint/EndpointPageClient.tsx). Returning only baseUrl/apiBase
          // left its setter unreached, so the page kept its hardcoded
          // OmniRoute default — it advertised localhost:20128 to every user
          // while the gateway was on ${PORT}. An endpoint screen that prints
          // the wrong port is worse than one that prints nothing: it is copied.
          localUrl: apiBase,
          // Nothing here can enumerate the host's LAN addresses — the bridge
          // runs in a container. Empty, not fabricated.
          lanUrls: [],
          capabilities: result.ok ? result.data : null,
          error: result.ok ? null : result.error,
        },
      };
    },
  },
  {
    pattern: "capabilities",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/capabilities", (data) => data),
  },

  // ── model catalog for the picker ──────────────────────────────────────────
  {
    pattern: "synced-available-models",
    method: "GET",
    handle: proxy<FastApiMyModels>("/v1/me/models", (data) => ({
      models: data.models.map((m) => ({
        id: m.model,
        name: m.model,
        provider: asArray(m.providers)[0] ?? "gateway",
        available: m.is_usable,
        mode: m.mode,
      })),
    })),
  },

  // ── probe progress ────────────────────────────────────────────────────────
  {
    pattern: "discovery/scan",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/probe-status", (data) => data),
  },

  // ── Key validation — the provider page's "Check" button ───────────────────
  // OmniRoute posts {provider, apiKey, baseUrl?} and reads back
  // {valid, error, warning, method}. The gateway tests the key against the
  // provider's /models endpoint — the same call discovery makes — and stores
  // nothing, so a failed check leaves no junk row behind.
  {
    pattern: "providers/validate",
    method: "POST",
    handle: async (ctx) => {
      let body: Record<string, unknown> = {};
      try {
        body = ctx.body ? (JSON.parse(ctx.body) as Record<string, unknown>) : {};
      } catch {
        return { status: 400, body: { error: "Invalid JSON body." } };
      }

      const psd = (body.providerSpecificData ?? {}) as Record<string, unknown>;
      const result = await callGateway<Record<string, unknown>>(
        "/v1/me/provider-keys/validate",
        {
          method: "POST",
          cookie: ctx.cookie,
          authorization: ctx.authorization,
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            provider: toGatewaySlug(String(body.provider ?? "")),
            api_key: body.apiKey ?? "",
            base_url: body.baseUrl ?? psd.baseUrl ?? null,
          }),
        }
      );
      if (!result.ok) return fail(result);

      const data = result.data as Record<string, unknown>;
      // OmniRoute's UI treats a non-2xx as a hard "Invalid" block. The gateway
      // answers 200 with a verdict in the body, so the shape is passed through
      // as-is: `valid: false` renders the message without the request itself
      // having failed.
      return {
        status: 200,
        body: {
          valid: Boolean(data.valid),
          error: data.error ?? null,
          warning: data.warning ?? null,
          method: data.method ?? null,
          modelCount: data.model_count ?? null,
          sampleModels: data.sample_models ?? [],
        },
      };
    },
  },

  // ── "Import from /models" — re-read the provider's catalog ────────────────
  // OmniRoute's sync-models. The gateway's equivalent re-reads the provider's
  // /models with the CALLER's key, upserts the catalog, re-fans it across their
  // keys and re-probes (SRS §9). Disable-never-delete applies, so a model that
  // vanished upstream is switched off rather than removed — deployments still
  // reference it.
  //
  // Falls back to the admin sync when the caller holds no key for the provider:
  // that path borrows any active key for a single read-only /models call, which
  // is the only way to populate a catalog before anyone has connected.
  {
    pattern: "providers/:slug/sync-models",
    method: "POST",
    handle: async (ctx, params) => {
      const slug = toGatewaySlug(params.slug);

      let result = await callGateway<Record<string, unknown>>(
        `/v1/me/providers/${slug}/discover`,
        { method: "POST", cookie: ctx.cookie, authorization: ctx.authorization }
      );

      if (!result.ok) {
        const adminAttempt = await callGateway<Record<string, unknown>>(
          `/v1/admin/providers/${slug}/discover`,
          { method: "POST", cookie: ctx.cookie, authorization: ctx.authorization }
        );
        // Report the ORIGINAL failure if the fallback also fails — the
        // user-scoped message ("you hold no active key for X") is the one that
        // says what to do about it.
        if (!adminAttempt.ok) return fail(result);
        result = adminAttempt;
      }

      const d = result.data as Record<string, unknown>;

      // The catalog AFTER the sync, so the count reflects what was just written
      // rather than what the sync call happened to report.
      const after = await callGateway<Record<string, unknown>>(
        `/v1/providers/${slug}/models`,
        { cookie: ctx.cookie, authorization: ctx.authorization }
      );
      const models = after.ok
        ? ((after.data as Record<string, unknown>).models as Array<Record<string, unknown>>) ?? []
        : [];

      const added = Number(d.added ?? 0);
      const updated = Number(d.updated ?? 0);

      return {
        status: 200,
        body: {
          ok: true,
          provider: slug,
          syncedModels: added + updated,
          importedCount: added,
          updatedCount: updated,
          availableModelsCount: models.length,
          disabledCount: d.disabled ?? 0,
          newDeployments: d.new_deployments ?? 0,
          detail: d.detail,
          models: models.map((m) => ({
            id: m.id,
            name: m.name,
            publisher: m.publisher,
            mode: m.mode,
            isFree: m.is_free,
            enabled: m.enabled,
          })),
          importedModels: [],
        },
      };
    },
  },

  // ── Provider catalog — the "Available Models" list ────────────────────────
  // OmniRoute calls /api/provider-models?provider=X. Answers what the PROVIDER
  // offers (a fact, visible before any key exists) rather than what the caller
  // can currently reach — so an unconfigured provider shows its catalog instead
  // of an empty list that reads as "this provider has no models".
  {
    pattern: "provider-models",
    method: "GET",
    handle: async (ctx) => {
      const params = new URLSearchParams(ctx.search);
      const raw = params.get("provider") ?? params.get("providerId") ?? "";
      if (!raw) return { status: 400, body: { error: "A provider is required." } };

      const result = await callGateway<Record<string, unknown>>(
        `/v1/providers/${toGatewaySlug(raw)}/models`,
        { cookie: ctx.cookie, authorization: ctx.authorization }
      );
      if (!result.ok) return fail(result);

      const data = result.data as Record<string, unknown>;
      const models = (data.models ?? []) as Array<Record<string, unknown>>;
      return {
        status: 200,
        body: {
          provider: data.provider,
          total: data.total ?? models.length,
          // OmniRoute's list renders {id, name, ...}; the extra gateway fields
          // ride along for anything that wants them.
          models: models.map((m) => ({
            id: m.id,
            name: m.name,
            displayName: m.name,
            publisher: m.publisher,
            mode: m.mode,
            contextWindow: m.context_window,
            maxOutputTokens: m.max_output_tokens,
            isFree: m.is_free,
            supportsStream: m.supports_stream,
            enabled: m.enabled,
            status: m.my_status,
            latencyMs: m.my_latency_ms,
            isCallable: m.my_is_callable,
          })),
        },
      };
    },
  },

  // ── Per-model test button ─────────────────────────────────────────────────
  // One real request with the stored key. Reports the same six statuses the
  // prober does, and deliberately does not write the result back — a manual
  // test is a question, not a re-ranking of routing health (SRS §13).
  {
    pattern: "models/test",
    method: "POST",
    handle: async (ctx) => {
      let body: Record<string, unknown> = {};
      try {
        body = ctx.body ? (JSON.parse(ctx.body) as Record<string, unknown>) : {};
      } catch {
        return { status: 400, body: { error: "Invalid JSON body." } };
      }
      const result = await callGateway<Record<string, unknown>>("/v1/me/models/test", {
        method: "POST",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          provider: toGatewaySlug(String(body.provider ?? body.providerId ?? "")),
          model: body.model ?? body.modelId ?? "",
        }),
      });
      if (!result.ok) return fail(result);
      const d = result.data as Record<string, unknown>;
      return {
        status: 200,
        body: {
          success: d.ok,
          ok: d.ok,
          status: d.status,
          statusCode: d.http_code,
          latencyMs: d.latency_ms,
          error: d.error,
          model: d.model,
        },
      };
    },
  },

  // ── Self-service connect — OmniRoute's "add connection" ───────────────────
  // The providers page posts {provider, apiKey, name, providerSpecificData:{baseUrl}}.
  // The gateway splits that into two acts: register the DESTINATION, then attach
  // the KEY. Doing both here is what lets a user connect a provider an admin
  // never seeded — the base URL comes from the request instead of presets.py.
  //
  // Adding the key is the step that triggers discovery, fan-out and the probe,
  // so the models appear on their own (SRS §6, §9, §13.3).
  {
    pattern: "providers",
    method: "POST",
    handle: async (ctx) => {
      let body: Record<string, unknown> = {};
      try {
        body = ctx.body ? (JSON.parse(ctx.body) as Record<string, unknown>) : {};
      } catch {
        return { status: 400, body: { error: "Invalid JSON body." } };
      }

      const slug = toGatewaySlug(String(body.provider ?? ""));
      if (!slug) return { status: 400, body: { error: "A provider id is required." } };

      const psd = (body.providerSpecificData ?? {}) as Record<string, unknown>;
      const baseUrl = (psd.baseUrl ?? body.baseUrl ?? "") as string;
      const name = (body.name ?? psd.nodeName ?? slug) as string;

      // Register the destination. 409 means it already exists, which is fine —
      // connecting a second key to a known provider is the common case.
      const registered = await callGateway<Record<string, unknown>>("/v1/admin/providers", {
        method: "POST",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          slug,
          name,
          ...(baseUrl ? { base_url: baseUrl } : {}),
        }),
      });

      if (!registered.ok && registered.status !== 409) {
        // A preset-less provider with no base URL cannot be registered, and the
        // gateway's message says exactly that — pass it through rather than
        // inventing one.
        return fail(registered);
      }

      const apiKey = String(body.apiKey ?? "").trim();
      if (!apiKey) {
        // Destination registered, no credential yet. Legitimate on its own.
        return {
          status: 201,
          body: {
            connection: { id: slug, provider: slug, name, isActive: false },
            detail: "Provider registered. Add a key to discover its models.",
          },
        };
      }

      const added = await callGateway<Record<string, unknown>>("/v1/me/provider-keys", {
        method: "POST",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ provider: slug, value: apiKey, label: body.name || undefined }),
      });
      if (!added.ok) return fail(added);

      const key = added.data as Record<string, unknown>;
      return {
        status: 201,
        body: {
          connection: {
            id: slug,
            provider: slug,
            name,
            isActive: true,
            keyId: key.id,
            masked: key.masked,
            modelsDiscovered: key.models_discovered ?? 0,
          },
          detail: key.detail,
        },
      };
    },
  },

  // ── SRS §6 — register a provider (admin) ──────────────────────────────────
  // A DESTINATION only: no key, no models. The catalog fills in when the first
  // key is added for it. This is what lets a card from OmniRoute's directory
  // that the backend has no preset for become a real, routable provider — the
  // user supplies the base URL the card does not carry.
  {
    pattern: "admin/providers",
    method: "POST",
    handle: proxy<Record<string, unknown>>("/v1/admin/providers", (data) => data),
  },
  {
    pattern: "admin/provider-presets",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/admin/provider-presets", (data) => data),
  },

  // ── SRS §9 — discovery, using the caller's own key ────────────────────────
  // Refreshes the provider's catalog, re-fans it out across the caller's keys
  // and re-probes them. Spends the CALLER's quota, never someone else's.
  {
    pattern: "providers/:slug/discover",
    method: "POST",
    handle: async (ctx, params) => {
      const result = await callGateway(`/v1/me/providers/${toGatewaySlug(params.slug)}/discover`, {
        method: "POST",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
      });
      if (!result.ok) return fail(result);
      return { status: 200, body: result.data ?? { success: true } };
    },
  },

  // ── SRS §13.3 — probe progress ────────────────────────────────────────────
  // Cheap by design (an in-memory lookup, no DB) because the UI polls it every
  // second or two while a progress bar is on screen.
  {
    pattern: "probe-status",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/probe-status", (data) => data),
  },

  // ── SRS §6 — provider keys, unreshaped ────────────────────────────────────
  // Distinct from `keys` above: that one squeezes the gateway's rows into the
  // shape OmniRoute's own pages expect and loses fields on the way. The API keys
  // screen is written for this gateway, so it reads the rows as they are.
  {
    pattern: "provider-keys",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/provider-keys", (data) => data),
  },
  {
    pattern: "provider-keys",
    method: "POST",
    handle: proxy<Record<string, unknown>>("/v1/me/provider-keys", (data) => data),
  },
  {
    pattern: "provider-keys/:id",
    method: "DELETE",
    handle: async (ctx, params) => {
      const result = await callGateway(`/v1/me/provider-keys/${params.id}`, {
        method: "DELETE",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
      });
      if (!result.ok) return fail(result);
      return { status: 200, body: { success: true } };
    },
  },
  {
    pattern: "provider-keys/:id/probe",
    method: "POST",
    handle: async (ctx, params) => {
      const result = await callGateway(`/v1/me/provider-keys/${params.id}/probe`, {
        method: "POST",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
      });
      if (!result.ok) return fail(result);
      return { status: 200, body: result.data ?? { success: true } };
    },
  },

  // ── SRS §6.1 — deployments, the unit of everything ────────────────────────
  // Passed through unreshaped: these screens were written for this gateway
  // against this endpoint, so there is no OmniRoute contract to translate into.
  {
    pattern: "deployments",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/deployments", (data) => data),
  },

  // ── SRS §20 — the dashboard surfaces ──────────────────────────────────────
  {
    pattern: "status-board",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/status-board", (data) => data),
  },
  {
    pattern: "health/timeline",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/health/timeline", (data) => data),
  },
  {
    pattern: "analytics/latency",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/analytics/latency", (data) => data),
  },
  {
    pattern: "errors",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/errors", (data) => data),
  },

  // ── SRS §5 — roles ────────────────────────────────────────────────────────
  // Answers honestly with two enforced roles and one declared-but-unenforced,
  // so the UI can render the SRS's three-role selector and disable what the
  // backend cannot honour. See backend/app/api/srs_gaps.py.
  {
    pattern: "roles",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/roles", (data) => data),
  },

  // ── SRS §4 — registration, declared but not implemented ───────────────────
  // Mapped deliberately rather than left to the catch-all: the gateway's 501
  // explains WHY there is no registration and what to do instead, which is more
  // useful than the bridge's generic "not wired yet".
  {
    pattern: "auth/register",
    method: "POST",
    handle: proxy<Record<string, unknown>>("/v1/auth/register", (data) => data),
  },

  // ── probe triggers ────────────────────────────────────────────────────────
  {
    pattern: "probe",
    method: "POST",
    handle: proxy<Record<string, unknown>>("/v1/me/probe", (data) => data),
  },

  // ── combos (OMNIROUTE_INTEGRATION Phase 1) ────────────────────────────────
  // The gateway speaks the builder's own camelCase shape (backend/app/api/
  // combos.py), so these are near-passthroughs. That is deliberate: the combo
  // screens are the densest contract in the ported UI, and a reshape here would
  // be a second place to keep in sync every time the builder grows a field.
  //
  // ORDER MATTERS. matchRoute walks this array and compares segment counts, so
  // the literal two-segment paths (`combos/metrics`, `combos/test`,
  // `combos/reorder`) MUST precede `combos/:id` — otherwise "metrics" is read as
  // a combo id and every metrics call 404s.
  {
    pattern: "combos",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/combos", (data) => data),
  },
  {
    pattern: "combos",
    method: "POST",
    handle: proxy<Record<string, unknown>>("/v1/me/combos", (data) => data),
  },
  {
    pattern: "combos/metrics",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/combos/metrics", (data) => data),
  },
  {
    pattern: "combos/test",
    method: "POST",
    handle: proxy<Record<string, unknown>>("/v1/me/combos/test", (data) => data),
  },
  {
    pattern: "combos/reorder",
    method: "POST",
    handle: proxy<Record<string, unknown>>("/v1/me/combos/reorder", (data) => data),
  },
  {
    // Three segments, so it cannot collide with `combos/:id` — listed here for
    // readability, not necessity.
    pattern: "combos/builder/options",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/combos/builder-options", (data) => data),
  },
  {
    pattern: "combos/:id",
    method: "GET",
    // The combo control centre reads the combo object DIRECTLY (not wrapped in
    // `{combo: …}`), which is what the gateway returns.
    handle: async (ctx, params) =>
      proxy<Record<string, unknown>>(`/v1/me/combos/${params.id}`, (data) => data)(ctx, params),
  },
  {
    pattern: "combos/:id",
    method: "PUT",
    handle: async (ctx, params) =>
      proxy<Record<string, unknown>>(`/v1/me/combos/${params.id}`, (data) => data)(ctx, params),
  },
  {
    pattern: "combos/:id",
    method: "DELETE",
    handle: async (ctx, params) => {
      const result = await callGateway(`/v1/me/combos/${params.id}`, {
        method: "DELETE",
        cookie: ctx.cookie,
        authorization: ctx.authorization,
      });
      if (!result.ok) return fail(result);
      return { status: 200, body: result.data ?? { success: true } };
    },
  },
  {
    // The combo routing playground. OmniRoute called it "simulate-route"; the
    // gateway calls it what it is.
    pattern: "playground/simulate-route",
    method: "POST",
    handle: proxy<Record<string, unknown>>("/v1/me/combos/simulate", (data) => data),
  },

  // ── combo builder side-loads ──────────────────────────────────────────────
  // The builder modal fetches these two ALONGSIDE the builder options and
  // THROWS if `models/alias` is not ok — which left the whole step editor empty
  // and every wizard stage after "Basics" locked. Both are answered here rather
  // than proxied, because the gateway genuinely has neither concept:
  //
  //   aliases  a user-defined second name for a model. The gateway resolves
  //            spellings itself (services/normalize.py), so there is nothing to
  //            alias and an empty map is the honest answer, not a stub.
  //   pricing  per-token prices. The catalog records is_free, not a rate card;
  //            an empty map makes the builder show "no pricing data" instead of
  //            inventing numbers that would then drive cost-optimized routing.
  {
    pattern: "models/alias",
    method: "GET",
    handle: async () => ({ status: 200, body: { aliases: {} } }),
  },
  {
    pattern: "pricing",
    method: "GET",
    handle: async () => ({ status: 200, body: {} }),
  },

  // ── combo defaults ────────────────────────────────────────────────────────
  // Prefill for a NEW combo, stored in the per-user settings KV. Read on every
  // create; written from Settings → Combo defaults.
  {
    pattern: "settings/combo-defaults",
    method: "GET",
    handle: proxy<Record<string, unknown>>("/v1/me/settings", (data) => ({
      comboDefaults: (data as { comboDefaults?: unknown }).comboDefaults ?? null,
    })),
  },
  {
    pattern: "settings/combo-defaults",
    method: "PUT",
    handle: async (ctx) => {
      // The settings endpoint takes a partial merge, so wrap the payload under
      // its key rather than replacing the whole preferences object.
      let parsed: unknown = {};
      try {
        parsed = ctx.body ? JSON.parse(ctx.body) : {};
      } catch {
        return { status: 400, body: { error: "Invalid JSON body." } };
      }
      const payload = (parsed as { comboDefaults?: unknown }).comboDefaults ?? parsed;
      const result = await callGateway<Record<string, unknown>>("/v1/me/settings", {
        method: "PUT",
        body: JSON.stringify({ comboDefaults: payload }),
        headers: { "content-type": "application/json" },
        cookie: ctx.cookie,
        authorization: ctx.authorization,
      });
      if (!result.ok) return fail(result);
      return { status: 200, body: { comboDefaults: payload } };
    },
  },
];

/**
 * Login/logout need the gateway's `Set-Cookie` relayed to the browser, which the
 * JSON-only `callGateway` helper deliberately drops.
 */
async function callGatewayWithHeaders(path: string, ctx: BridgeContext): Promise<BridgeResponse> {
  const { GATEWAY_URL } = await import("./gateway");
  let response: Response;
  try {
    response = await fetch(`${GATEWAY_URL}${path}`, {
      method: ctx.method,
      headers: {
        "content-type": "application/json",
        ...(ctx.cookie ? { cookie: ctx.cookie } : {}),
        ...(ctx.authorization ? { authorization: ctx.authorization } : {}),
      },
      body: ctx.body ?? undefined,
      cache: "no-store",
    });
  } catch (error) {
    return {
      status: 502,
      body: { error: `Cannot reach the gateway: ${(error as Error).message}` },
    };
  }

  const text = await response.text();
  let parsed: unknown = {};
  try {
    parsed = text ? JSON.parse(text) : {};
  } catch {
    parsed = { error: text };
  }

  const headers: Record<string, string> = {};
  const setCookie = response.headers.get("set-cookie");
  if (setCookie) headers["set-cookie"] = setCookie;

  return { status: response.status, body: parsed, headers };
}

/** Resolve a request to a route, extracting `:param` values. */
export function matchRoute(
  segments: string[],
  method: string
): { route: BridgeRoute; params: Record<string, string> } | null {
  for (const route of ROUTES) {
    if (route.method !== method) continue;
    const parts = route.pattern.split("/");
    if (parts.length !== segments.length) continue;

    const params: Record<string, string> = {};
    let matched = true;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      if (part.startsWith(":")) {
        params[part.slice(1)] = segments[i];
      } else if (part !== segments[i]) {
        matched = false;
        break;
      }
    }
    if (matched) return { route, params };
  }
  return null;
}
