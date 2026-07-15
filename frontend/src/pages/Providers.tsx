/**
 * Providers & Keys — where a user adds their OWN API keys.
 *
 * THE ASYNC FLOW IS THE HARD PART. POST /v1/me/provider-keys returns immediately
 * with {status: "probing"}: the key is fanned out into one deployment per model
 * that provider serves (~30 for Groq), and those are probed in the BACKGROUND, a
 * few at a time — because firing 30 simultaneous requests at a free tier would
 * rate-limit the very key we're validating.
 *
 * So the UI must show the key appearing instantly in a "testing…" state and poll
 * until the counts settle. Rendered as an ordinary synchronous form, this would
 * look broken for ~20 seconds.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "../lib/api";
import type { MyProviderKey, ProviderInfo } from "../lib/types";
import { Spinner, StatusDot } from "../components/ui";
import ProbeProgress from "../components/ProbeProgress";

export default function Providers() {
  const qc = useQueryClient();
  // false = closed · true = open, user picks · slug = open, preselected
  const [adding, setAdding] = useState<boolean | string>(false);

  const providers = useQuery({ queryKey: ["providers"], queryFn: api.providers });

  const keys = useQuery({
    queryKey: ["my-keys"],
    queryFn: api.myProviderKeys,
    // Poll while a key is genuinely being probed. "Genuinely" matters: a key
    // with 0 deployments on a provider whose CATALOG is also empty is not
    // probing — discovery failed (bad key / wrong URL) — and polling it forever
    // would just spin. Only poll when the catalog has models the fan-out can
    // still land on.
    refetchInterval: (q) => {
      const data = q.state.data as { keys: MyProviderKey[] } | undefined;
      const catalog = new Map(
        (providers.data?.providers ?? []).map((p) => [p.slug, p.model_count]),
      );
      const probing = data?.keys.some(
        (k) => k.total_models === 0 && (catalog.get(k.provider) ?? 0) > 0,
      );
      return probing ? 2000 : false;
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteProviderKey(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["my-keys"] });
      qc.invalidateQueries({ queryKey: ["my-models"] });
      qc.invalidateQueries({ queryKey: ["providers"] });
    },
  });

  // Per-provider "Discover models": refresh the catalog with MY key, fan any new
  // models out into deployments, and re-probe all my keys for that provider.
  // Also the recovery lever when a key shows 0 working after a flaky probe run.
  const [discovering, setDiscovering] = useState<string | null>(null);
  const [discoverMsg, setDiscoverMsg] = useState<string | null>(null);
  const discover = useMutation({
    mutationFn: (slug: string) => api.discoverMyProvider(slug),
    onMutate: (slug) => {
      setDiscovering(slug);
      setDiscoverMsg(null);
    },
    onSuccess: (r) => {
      setDiscoverMsg(r.detail);
      qc.invalidateQueries({ queryKey: ["providers"] });
      qc.invalidateQueries({ queryKey: ["my-keys"] });
      qc.invalidateQueries({ queryKey: ["my-models"] });
    },
    onError: (e: ApiError) => setDiscoverMsg(e.message),
    onSettled: () => setDiscovering(null),
  });

  // Per-provider "Test keys": probe-only, no catalog refresh, no /models call.
  // The cheap way to answer "did fixing that key revive this provider?".
  const [testing, setTesting] = useState<string | null>(null);
  const testProvider = useMutation({
    mutationFn: (slug: string) => api.reprobeProvider(slug),
    onMutate: (slug) => {
      setTesting(slug);
      setDiscoverMsg(null);
    },
    onSuccess: (r) => {
      setDiscoverMsg(r.detail);
      // Probes land in the background — refresh the counts as they settle.
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["my-keys"] });
        qc.invalidateQueries({ queryKey: ["my-models"] });
      }, 3000);
    },
    onError: (e: ApiError) => setDiscoverMsg(e.message),
    onSettled: () => setTesting(null),
  });

  // "Download backup": a CSV of every key, DECRYPTED — the originals, reusable
  // as-is after a reinstall or lost database. The file is the secret; the UI
  // never sees the plaintext.
  const exportKeys = useMutation({
    mutationFn: api.exportProviderKeys,
    onMutate: () => setDiscoverMsg(null),
    onError: (e: ApiError) => setDiscoverMsg(e.message),
  });

  // "Test all keys": every provider, every key — same as Models' Re-test all.
  const testAll = useMutation({
    mutationFn: api.reprobe,
    onMutate: () => setDiscoverMsg(null),
    onSuccess: () => {
      setDiscoverMsg("Re-testing every key across all providers in the background.");
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["my-keys"] });
        qc.invalidateQueries({ queryKey: ["my-models"] });
      }, 3000);
    },
    onError: (e: ApiError) => setDiscoverMsg(e.message),
  });

  if (providers.isLoading || keys.isLoading) return <Spinner />;

  const all = providers.data?.providers ?? [];
  const mine = keys.data?.keys ?? [];

  if (all.length === 0) {
    return (
      <div className="card">
        <h2 className="text-base font-medium text-neutral-200">No providers yet</h2>
        <p className="mt-2 text-sm leading-relaxed text-neutral-500">
          An admin has to seed a provider (Groq, NVIDIA NIM, …) before you can
          attach a key to it. Ask an admin to add one.
        </p>
      </div>
    );
  }

  // Group my keys under their provider.
  const byProvider = new Map<string, MyProviderKey[]>();
  for (const k of mine) {
    byProvider.set(k.provider, [...(byProvider.get(k.provider) ?? []), k]);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-neutral-100">Providers & keys</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Your keys, encrypted at rest. They are never shown again after you save them.
          </p>
        </div>
        <div className="flex gap-2">
          {mine.length > 0 && (
            <button
              className="btn-sec"
              onClick={() => exportKeys.mutate()}
              disabled={exportKeys.isPending}
              title="Download all your provider keys, unmasked, as a CSV backup. Keep the file somewhere safe — it contains the original secrets."
            >
              {exportKeys.isPending ? "Preparing…" : "⬇ Backup keys"}
            </button>
          )}
          {mine.length > 0 && (
            <button
              className="btn-sec"
              onClick={() => testAll.mutate()}
              disabled={testAll.isPending || testing !== null || discovering !== null}
              title="Re-test every key at every provider (same as Re-test all on the Models page)."
            >
              {testAll.isPending ? "Testing…" : "Test all keys"}
            </button>
          )}
          <button className="btn-pri" onClick={() => setAdding(true)}>+ Add key</button>
        </div>
      </div>

      <ProbeProgress />

      {discoverMsg && (
        <div className="rounded-xl border border-neutral-700 bg-neutral-900/60 px-4 py-3 text-sm text-neutral-300">
          {discoverMsg}
        </div>
      )}

      <div className="space-y-4">
        {all.map((p) => {
          const ks = byProvider.get(p.slug) ?? [];
          return (
            <div key={p.id} className="card">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h2 className="font-medium text-neutral-200">{p.name}</h2>
                  <p className="text-xs text-neutral-600">
                    {p.model_count} models in the catalog
                  </p>
                </div>
                <div className="flex gap-2">
                  {ks.length > 0 && (
                    <button
                      className="btn-sec text-xs"
                      onClick={() => testProvider.mutate(p.slug)}
                      disabled={testing !== null || discovering !== null}
                      title="Re-test your keys against this provider's existing catalog. No discovery call, other providers untouched."
                    >
                      {testing === p.slug ? "Testing…" : "Test keys"}
                    </button>
                  )}
                  {ks.length > 0 && (
                    <button
                      className="btn-sec text-xs"
                      onClick={() => discover.mutate(p.slug)}
                      disabled={discovering !== null || testing !== null}
                      title="Refresh this provider's model list using your key, then re-test all your keys for it."
                    >
                      {discovering === p.slug ? "Discovering…" : "Discover models"}
                    </button>
                  )}
                  <button className="btn-sec text-xs" onClick={() => setAdding(p.slug)}>
                    + Add a {p.name} key
                  </button>
                </div>
              </div>


              {ks.length === 0 ? (
                <p className="text-sm text-neutral-600">No key — you can't call these models.</p>
              ) : (
                <ul className="space-y-2">
                  {ks.map((k) => {
                    // Three states, not two. A key with no deployments on a
                    // provider with an EMPTY catalog is not "testing" — its
                    // discovery failed (bad key, wrong URL), and saying
                    // "testing…" forever hides that from the user.
                    const probing = k.total_models === 0 && p.model_count > 0;
                    const noModels = k.total_models === 0 && p.model_count === 0;
                    return (
                      <li
                        key={k.id}
                        className="flex items-center gap-3 rounded-lg border border-neutral-800 px-3 py-2.5"
                      >
                        <StatusDot ok={k.working_models > 0} cooling={probing} />
                        <span className="text-sm text-neutral-200">{k.label}</span>
                        <span className="font-mono text-xs text-neutral-600">{k.masked}</span>
                        <span className="ml-auto text-xs text-neutral-500">
                          {probing ? (
                            <span className="text-amber-400">testing models…</span>
                          ) : noModels ? (
                            <span className="text-red-400">
                              no models discovered — key may be invalid; delete and re-add
                            </span>
                          ) : (
                            `${k.working_models}/${k.total_models} models working`
                          )}
                        </span>
                        <button
                          className="btn-dng text-xs"
                          onClick={() => remove.mutate(k.id)}
                          disabled={remove.isPending}
                        >
                          Delete
                        </button>
                      </li>
                    );
                  })}

                  {/* The whole point of allowing several keys per provider. */}
                  {ks.length === 1 && (
                    <li className="px-3 pt-1 text-xs leading-relaxed text-neutral-600">
                      Add a second {p.name} key to double your free-tier budget — when
                      one is rate-limited, the other keeps serving.
                    </li>
                  )}
                </ul>
              )}
            </div>
          );
        })}
      </div>

      {adding !== false && (
        <AddKeyDialog
          providers={all}
          initialProvider={typeof adding === "string" ? adding : undefined}
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false);
            qc.invalidateQueries({ queryKey: ["my-keys"] });
            qc.invalidateQueries({ queryKey: ["providers"] });
            qc.invalidateQueries({ queryKey: ["me"] });
          }}
        />
      )}
    </div>
  );
}

function AddKeyDialog({
  providers, initialProvider, onClose, onSaved,
}: {
  providers: ProviderInfo[];
  /** Set when opened from a provider card's own "+ Add a X key" button. */
  initialProvider?: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [provider, setProvider] = useState(initialProvider ?? providers[0]?.slug ?? "");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () =>
      api.addProviderKey({ provider, value, label: label.trim() || undefined }),
    onSuccess: onSaved,
    onError: (e: ApiError) => setError(e.message),
  });

  const p = providers.find((x) => x.slug === provider);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="card w-full max-w-md">
        <h2 className="mb-4 font-medium text-neutral-100">Add a provider key</h2>

        <label className="label">Provider</label>
        {/* A dropdown, not a text field: you can only attach keys to providers an
            admin has seeded. */}
        <select
          className="input mb-4"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
        >
          {providers.map((x) => (
            <option key={x.slug} value={x.slug}>
              {x.name} ({x.model_count} models)
            </option>
          ))}
        </select>

        {/* Which KIND of key this provider wants — shown at the exact moment
            someone is about to paste the wrong kind (GitHub: fine-grained PAT
            with Models:read; a classic ghp_ token just 401s). */}
        {(p?.key_hint || p?.docs_url) && (
          <div className="mb-4 rounded-lg border border-neutral-800 bg-neutral-950 px-3 py-2.5">
            {p?.key_hint && (
              <div className="text-xs text-neutral-400">{p.key_hint}</div>
            )}
            {p?.docs_url && (
              <a
                href={p.docs_url}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-block text-xs text-emerald-500 hover:underline"
              >
                Get an API key →
              </a>
            )}
          </div>
        )}

        <label className="label">API key</label>
        <input
          className="input mb-1 font-mono"
          type="password"
          placeholder="gsk_…"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <p className="mb-4 text-xs text-neutral-600">
          Encrypted before it's stored. It is never returned by the API again.
        </p>

        <label className="label">Label (optional)</label>
        <input
          className="input mb-4"
          placeholder={`${p?.name ?? "Provider"} account #1`}
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />

        {error && (
          <p className="mb-3 rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-sm text-red-400">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button className="btn-sec" onClick={onClose}>Cancel</button>
          <button
            className="btn-pri"
            disabled={!value.trim() || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : "Add key"}
          </button>
        </div>

        <p className="mt-4 text-xs leading-relaxed text-neutral-600">
          We'll test every model this provider serves in the background — that
          takes a few seconds and happens a few at a time, so we don't rate-limit
          the key we're checking.
        </p>
      </div>
    </div>
  );
}
