/**
 * API client.
 *
 * AUTH: the gateway key IS the credential — there is no login, no session, no
 * cookie. We keep it in localStorage and send it as a Bearer token.
 *
 * On any 401 we clear the key and reload, which drops the user back at the key
 * screen. That is the correct response to a revoked key: the app cannot recover
 * by retrying, and silently showing empty pages would be worse than saying so.
 */

import type {
  AddProviderResult, AdminUser, LogRow, Me, MintedKey, MyModel, MyProviderKey,
  Preset, ProviderInfo, RouterConfig, Usage,
} from "./types";

const KEY_STORAGE = "gateway_key";

export function getKey(): string | null {
  return localStorage.getItem(KEY_STORAGE);
}

export function setKey(key: string): void {
  localStorage.setItem(KEY_STORAGE, key.trim());
}

export function clearKey(): void {
  localStorage.removeItem(KEY_STORAGE);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  key?: string,
): Promise<T> {
  const token = key ?? getKey();

  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });

  if (res.status === 401 && !key) {
    // The stored key is dead. Nothing here can recover; send them back to the gate.
    clearKey();
    window.location.reload();
    throw new ApiError(401, "Key is invalid or revoked.");
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body.message ?? detail;
      if (Array.isArray(detail)) detail = detail.map((d: any) => d.msg).join(", ");
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, String(detail));
  }

  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  /** Validates a key WITHOUT storing it — used by the key gate before committing. */
  verifyKey: (key: string) => request<Me>("/v1/me", {}, key),

  me: () => request<Me>("/v1/me"),
  providers: () => request<{ providers: ProviderInfo[] }>("/v1/providers"),

  // ── my keys ──
  myProviderKeys: () => request<{ keys: MyProviderKey[] }>("/v1/me/provider-keys"),
  addProviderKey: (body: { provider: string; value: string; label?: string }) =>
    request<{ id: number; status: string }>("/v1/me/provider-keys", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteProviderKey: (id: number) =>
    request<void>(`/v1/me/provider-keys/${id}`, { method: "DELETE" }),
  /**
   * Download my provider keys — decrypted — as a CSV backup. Not request<T>:
   * the response is a file, not JSON, and we hand it straight to the browser's
   * download machinery without ever holding the plaintext in app state.
   */
  exportProviderKeys: async (): Promise<void> => {
    const token = getKey();
    const res = await fetch("/v1/me/provider-keys/export", {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch { /* non-JSON */ }
      throw new ApiError(res.status, String(detail));
    }
    const blob = await res.blob();
    const filename =
      res.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] ??
      "provider-keys-backup.csv";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },

  // ── my models ──
  myModels: () => request<{ models: MyModel[] }>("/v1/me/models"),
  reprobe: () => request<{ status: string }>("/v1/me/probe", { method: "POST" }),
  // Refresh a provider's catalog with MY key, then re-fan-out + re-probe my keys.
  discoverMyProvider: (slug: string) =>
    request<{ status: string; added: number; detail: string }>(
      `/v1/me/providers/${slug}/discover`,
      { method: "POST" },
    ),
  // Probe-only sibling of discover: re-test my keys at ONE provider, no catalog
  // refresh, other providers untouched.
  reprobeProvider: (slug: string) =>
    request<{ status: string; keys: number; detail: string }>(
      `/v1/me/providers/${slug}/probe`,
      { method: "POST" },
    ),
  // How far along my background probes are — powers the progress bar.
  probeStatus: () =>
    request<{ active: boolean; done: number; total: number }>("/v1/me/probe-status"),

  // ── analytics ──
  usage: (days = 30) => request<Usage>(`/v1/me/usage?days=${days}`),
  logs: (limit = 50, status?: "ok" | "error") =>
    request<{ total: number; logs: LogRow[] }>(
      `/v1/me/logs?limit=${limit}${status ? `&status=${status}` : ""}`,
    ),

  // ── router config ──
  routerConfig: () => request<RouterConfig>("/v1/me/router-config"),
  updateRouterConfig: (body: Partial<RouterConfig>) =>
    request<RouterConfig>("/v1/me/router-config", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  resetRouterConfig: () =>
    request<RouterConfig>("/v1/me/router-config", { method: "DELETE" }),

  // ── chat (playground) ──
  chat: (model: string, content: string) =>
    request<any>("/v1/chat/completions", {
      method: "POST",
      body: JSON.stringify({ model, messages: [{ role: "user", content }] }),
    }),

  /**
   * Streaming chat. Calls onDelta with each text fragment as it arrives — this
   * is what makes the playground feel like a chat instead of a form that hangs
   * for ten seconds and then dumps a paragraph.
   *
   * The gateway emits OpenAI-style SSE: `data: {json}` lines ending with
   * `data: [DONE]`. Errors mid-stream arrive as a `data: {"error": …}` line
   * (the HTTP status is already 200 by then), so we surface those too.
   */
  chatStream: async (
    body: { model: string; messages: { role: string; content: string }[] },
    onDelta: (text: string) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const token = getKey();
    const res = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ ...body, stream: true }),
      signal,
    });

    if (res.status === 401) {
      clearKey();
      window.location.reload();
      throw new ApiError(401, "Key is invalid or revoked.");
    }
    if (!res.ok || !res.body) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch { /* non-JSON */ }
      throw new ApiError(res.status, String(detail));
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are newline-separated; a chunk may hold several or half of one.
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const data = line.replace(/^data:\s*/, "").trim();
        if (!data || data === "[DONE]") continue;
        try {
          const parsed = JSON.parse(data);
          if (parsed.error) throw new ApiError(502, parsed.error.message ?? "stream error");
          const delta = parsed.choices?.[0]?.delta?.content;
          if (delta) onDelta(delta);
        } catch (e) {
          if (e instanceof ApiError) throw e;
          /* partial frame — ignored, completed by the next chunk */
        }
      }
    }
  },

  // ── admin ──
  admin: {
    providers: () => request<{ providers: ProviderInfo[] }>("/v1/admin/providers"),
    presets: () => request<{ presets: Preset[] }>("/v1/admin/provider-presets"),
    // No api_key here: keys are added on Providers & keys, where the first key
    // for an empty-catalog provider triggers model auto-discovery.
    addProvider: (body: { slug: string; name?: string; base_url?: string }) =>
      request<AddProviderResult>("/v1/admin/providers", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    toggleProvider: (slug: string, enabled: boolean) =>
      request<{ slug: string; enabled: boolean }>(`/v1/admin/providers/${slug}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      }),
    discover: (slug: string) =>
      request<{ added?: number; updated?: number; error?: string }>(
        `/v1/admin/providers/${slug}/discover`,
        { method: "POST" },
      ),
    deleteProvider: (slug: string) =>
      request<{ status: string; models_removed: number; provider_keys_removed: number }>(
        `/v1/admin/providers/${slug}`,
        { method: "DELETE" },
      ),
    users: () => request<{ users: AdminUser[] }>("/v1/admin/users"),
    createUser: (body: { email?: string; is_admin: boolean }) =>
      request<AdminUser>("/v1/admin/users", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    mintKey: (user_id: number, name = "default") =>
      request<MintedKey>("/v1/admin/gateway-keys", {
        method: "POST",
        body: JSON.stringify({ user_id, name }),
      }),
    userKeys: (id: number) =>
      request<{ gateway_keys: any[]; provider_keys: MyProviderKey[] }>(
        `/v1/admin/users/${id}/keys`,
      ),
    revokeKey: (id: number) =>
      request<void>(`/v1/admin/gateway-keys/${id}`, { method: "DELETE" }),
  },
};
