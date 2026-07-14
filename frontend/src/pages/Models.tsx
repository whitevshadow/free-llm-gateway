/**
 * My Models — what this user can actually call, and how resilient each one is.
 *
 * Reads v_my_models, which is built ONLY from this user's deployments. A model
 * they hold no key for cannot appear here — not because we filter it out, but
 * because the row cannot exist (it only exists through their key, FK-enforced).
 *
 * TWO FILTER AXES, ONE AT A TIME (deliberate, per design decision):
 *   • cards (providers + Embeddings) — "what can I reach through X"
 *   • the publisher panel on the right — "everything by Meta, wherever it runs"
 * Selecting on one axis clears the other; mixing them ("Meta via Groq") reads
 * as power but mostly produces confusing empty tables.
 *
 * The unavailable toggle intersects with whichever axis is active — hiding dead
 * rows is a display preference, not a filter.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../lib/api";
import type { MyModel } from "../lib/types";
import { Empty, RedundancyBadge, Spinner, StatusDot } from "../components/ui";
import ProbeProgress from "../components/ProbeProgress";

interface ProviderGroup {
  name: string;
  models: MyModel[];
  live: number;
  exposed: number;   // usable but with no backup — the actionable number
}

export default function Models() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["my-models"], queryFn: api.myModels });

  const [selected, setSelected] = useState<string | null>(null);
  const [publishers, setPublishers] = useState<Set<string>>(new Set());
  // Unavailable models are HIDDEN by default: with NVIDIA, more than half the
  // catalog can't answer a probe, and a wall of grey rows buries the live ones.
  const [showUnavailable, setShowUnavailable] = useState(false);

  const reprobe = useMutation({
    mutationFn: api.reprobe,
    onSuccess: () => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ["my-models"] }), 3000);
    },
  });

  const models = data?.models ?? [];
  const chatModels = useMemo(
    () => models.filter((m) => m.mode !== "embedding"), [models],
  );
  const embModels = useMemo(
    () => models.filter((m) => m.mode === "embedding"), [models],
  );

  // Provider cards are a lens over CHAT models; a multi-provider model appears
  // under each of its providers.
  const groups = useMemo<ProviderGroup[]>(() => {
    const by = new Map<string, MyModel[]>();
    for (const m of chatModels) {
      for (const p of m.providers) {
        by.set(p, [...(by.get(p) ?? []), m]);
      }
    }
    return [...by.entries()]
      .map(([name, ms]) => ({
        name,
        models: ms,
        live: ms.filter((m) => m.is_usable).length,
        exposed: ms.filter((m) => m.is_usable && !m.has_backup_key).length,
      }))
      .sort((a, b) => b.models.length - a.models.length);
  }, [chatModels]);

  // Publisher counts across ALL models (chat + embedding), sorted by count —
  // the right-hand panel, mirroring build.nvidia.com's filter.
  const publisherCounts = useMemo(() => {
    const by = new Map<string, number>();
    for (const m of models) {
      const pub = m.publisher ?? "Unknown";
      by.set(pub, (by.get(pub) ?? 0) + 1);
    }
    return [...by.entries()].sort((a, b) => b[1] - a[1]);
  }, [models]);

  if (isLoading) return <Spinner />;

  if (models.length === 0) {
    return (
      <Empty
        title="No models yet"
        body="You can only call models you hold a provider key for. Add a key and it
              fans out into every model that provider serves."
        action={<Link to="/providers" className="btn-pri">Add a provider key</Link>}
      />
    );
  }

  const exposed = chatModels.filter((m) => m.is_usable && !m.has_backup_key).length;
  const embSelected = selected === "__embeddings__";
  const active = selected && !embSelected ? groups.find((g) => g.name === selected) : null;

  // One axis at a time: publishers win when any are ticked.
  const inScope =
    publishers.size > 0
      ? models.filter((m) => publishers.has(m.publisher ?? "Unknown"))
      : embSelected
      ? embModels
      : active
      ? active.models
      : chatModels;

  const hiddenCount = inScope.filter((m) => !m.is_usable).length;
  const shown = showUnavailable ? inScope : inScope.filter((m) => m.is_usable);

  function pickCard(name: string | null) {
    setSelected(name);
    setPublishers(new Set());     // cards replace the publisher axis
  }

  function togglePublisher(pub: string) {
    setSelected(null);            // publishers replace the card axis
    setPublishers((cur) => {
      const next = new Set(cur);
      if (next.has(pub)) next.delete(pub);
      else next.add(pub);
      return next;
    });
  }

  const tableTitle =
    publishers.size > 0
      ? `${[...publishers].join(", ")} — ${shown.length} models`
      : embSelected
      ? `Embeddings — ${shown.length} models`
      : active
      ? `${active.name} — ${shown.length} models`
      : `${shown.length} chat models`;

  return (
    <div className="flex gap-6">
      {/* ── main column ── */}
      <div className="min-w-0 flex-1 space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-neutral-100">My models</h1>
            <p className="mt-1 text-sm text-neutral-500">
              {models.length} models across {groups.length} provider
              {groups.length === 1 ? "" : "s"} · request them by name from any OpenAI client.
            </p>
          </div>
          <button
            className="btn-sec"
            onClick={() => reprobe.mutate()}
            disabled={reprobe.isPending}
          >
            {reprobe.isPending ? "Testing…" : "Re-test all"}
          </button>
        </div>

        <ProbeProgress />

        {/* Only LIVE models count here — no backup of nothing is not news. */}
        {exposed > 0 && (
          <div className="rounded-xl border border-amber-900/50 bg-amber-950/20 px-4 py-3 text-sm text-amber-300">
            <strong className="font-medium">
              {exposed} live model{exposed === 1 ? "" : "s"} with no backup.
            </strong>{" "}
            If that one key hits its rate limit, they go dark. Adding a second key —
            even at the same provider — gives them somewhere to fall back to.
          </div>
        )}

        {/* ── cards: providers (chat) + embeddings ── */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {groups.map((g) => {
            const isSel = selected === g.name;
            return (
              <button
                key={g.name}
                onClick={() => pickCard(isSel ? null : g.name)}
                className={`card text-left transition-colors ${
                  isSel ? "border-emerald-700 bg-emerald-950/20" : "hover:border-neutral-700"
                }`}
              >
                <div className="flex items-center justify-between">
                  <h2 className="font-medium text-neutral-100">{g.name}</h2>
                  <span className={`text-xs ${isSel ? "text-emerald-400" : "text-neutral-600"}`}>
                    {isSel ? "showing ▾" : "click to view"}
                  </span>
                </div>
                <div className="mt-3 flex items-end gap-5">
                  <div>
                    <div className="text-2xl font-semibold tabular-nums text-neutral-100">
                      {g.live}
                      <span className="text-sm font-normal text-neutral-600">
                        /{g.models.length}
                      </span>
                    </div>
                    <div className="text-xs text-neutral-600">models live</div>
                  </div>
                  {g.exposed > 0 && (
                    <div>
                      <div className="text-2xl font-semibold tabular-nums text-amber-400">
                        {g.exposed}
                      </div>
                      <div className="text-xs text-neutral-600">no backup</div>
                    </div>
                  )}
                </div>
              </button>
            );
          })}

          {/* Embeddings are a different surface (/v1/embeddings), hence a
              different-coloured card rather than rows mixed into chat counts. */}
          {embModels.length > 0 && (
            <button
              onClick={() => pickCard(embSelected ? null : "__embeddings__")}
              className={`card text-left transition-colors ${
                embSelected ? "border-purple-700 bg-purple-950/20" : "hover:border-neutral-700"
              }`}
            >
              <div className="flex items-center justify-between">
                <h2 className="font-medium text-purple-300">Embeddings</h2>
                <span className={`text-xs ${embSelected ? "text-purple-400" : "text-neutral-600"}`}>
                  {embSelected ? "showing ▾" : "click to view"}
                </span>
              </div>
              <div className="mt-3 flex items-end gap-5">
                <div>
                  <div className="text-2xl font-semibold tabular-nums text-neutral-100">
                    {embModels.filter((m) => m.is_usable).length}
                    <span className="text-sm font-normal text-neutral-600">
                      /{embModels.length}
                    </span>
                  </div>
                  <div className="text-xs text-neutral-600">models live</div>
                </div>
                <div className="pb-1 text-xs text-neutral-600">via /v1/embeddings</div>
              </div>
            </button>
          )}
        </div>

        {/* ── the model table ── */}
        <div className="card p-0">
          <div className="flex items-center justify-between border-b border-neutral-800 px-4 py-3">
            <h3 className="text-sm font-medium text-neutral-300">{tableTitle}</h3>
            <div className="flex items-center gap-4">
              {hiddenCount > 0 && (
                <button
                  className="text-xs text-neutral-500 hover:text-neutral-300"
                  onClick={() => setShowUnavailable((v) => !v)}
                  title="Unavailable = the probe couldn't get a reply (rate limits, dead keys, models not served to your account). Hidden so the usable list stays readable."
                >
                  {showUnavailable ? "hide unavailable" : `show ${hiddenCount} unavailable`}
                </button>
              )}
              {(active || embSelected || publishers.size > 0) && (
                <button
                  className="text-xs text-neutral-500 hover:text-neutral-300"
                  onClick={() => { setSelected(null); setPublishers(new Set()); }}
                >
                  show all ✕
                </button>
              )}
            </div>
          </div>
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">Model</th>
                <th className="th">Publisher</th>
                <th className="th">Providers</th>
                <th className="th">Live keys</th>
                <th className="th">Redundancy</th>
                <th className="th">Status</th>
              </tr>
            </thead>
            <tbody>
              {shown.length === 0 && (
                <tr>
                  <td className="td text-neutral-600" colSpan={6}>
                    No live models here — {hiddenCount} unavailable hidden.
                  </td>
                </tr>
              )}
              {shown.map((m) => (
                <tr key={`${m.model}-${m.mode}`} className="hover:bg-neutral-900/40">
                  <td className="td">
                    <span className="font-mono text-neutral-100">{m.model}</span>
                    {m.mode === "embedding" && (
                      <span className="ml-2 rounded bg-purple-950/60 px-1.5 py-0.5 text-[10px] text-purple-400">
                        embedding
                      </span>
                    )}
                    {/* is_common is a catalog badge — NOT a promise that YOU have
                        a fallback. Rendered dim, never as reassurance. */}
                    {m.is_common && (
                      <span
                        className="ml-2 rounded bg-neutral-800 px-1.5 py-0.5 text-[10px] text-neutral-500"
                        title="Two or more providers serve this model in the catalog. That doesn't mean you hold keys to them."
                      >
                        common
                      </span>
                    )}
                  </td>
                  <td className="td text-neutral-400">{m.publisher ?? "—"}</td>
                  <td className="td text-neutral-400">{m.providers.join(", ")}</td>
                  <td className="td tabular-nums text-neutral-400">
                    {m.live_keys}/{m.total_keys}
                  </td>
                  <td className="td"><RedundancyBadge model={m} /></td>
                  <td className="td">
                    <span className="inline-flex items-center gap-2">
                      <StatusDot ok={m.is_usable} />
                      <span className={m.is_usable ? "text-neutral-300" : "text-neutral-600"}>
                        {m.is_usable ? "Live" : "Unavailable"}
                      </span>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── right sidebar: publisher filter (like build.nvidia.com's) ── */}
      <aside className="hidden w-56 shrink-0 lg:block">
        <div className="card sticky top-6">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-neutral-200">Publisher</h2>
            {publishers.size > 0 && (
              <button
                className="text-xs text-neutral-500 hover:text-neutral-300"
                onClick={() => setPublishers(new Set())}
              >
                Reset
              </button>
            )}
          </div>
          <ul className="max-h-[70vh] space-y-1 overflow-y-auto pr-1">
            {publisherCounts.map(([pub, count]) => {
              const checked = publishers.has(pub);
              return (
                <li key={pub}>
                  <label
                    className={`flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm
                                transition-colors hover:bg-neutral-800/60 ${
                                  checked ? "text-neutral-100" : "text-neutral-400"
                                }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => togglePublisher(pub)}
                      className="h-4 w-4 rounded accent-emerald-600"
                    />
                    <span className="flex-1 truncate">{pub}</span>
                    <span className="tabular-nums text-xs text-neutral-600">{count}</span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      </aside>
    </div>
  );
}
