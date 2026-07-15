/**
 * My Models — what this user can actually call, and how resilient each one is.
 *
 * Reads v_my_models, which is built ONLY from this user's deployments. A model
 * they hold no key for cannot appear here — not because we filter it out, but
 * because the row cannot exist (it only exists through their key, FK-enforced).
 *
 * FILTERING: one combinable filter bar (search + Provider + Publisher +
 * Status + Redundancy dropdowns). Every control narrows the table together —
 * OR within a dropdown, AND across dropdowns. The provider cards are shortcuts
 * that toggle the Provider filter; the Embeddings card switches the table to
 * embedding models (a different surface, /v1/embeddings, so it's a mode switch
 * rather than a filter).
 *
 * Status defaults to Live: with NVIDIA, more than half the catalog can't
 * answer a probe, and a wall of grey rows buries the usable ones.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../lib/api";
import type { MyModel } from "../lib/types";
import { Empty, FilterDropdown, RedundancyBadge, Spinner, statusInfo } from "../components/ui";
import ProbeProgress from "../components/ProbeProgress";

interface ProviderGroup {
  name: string;
  models: MyModel[];
  live: number;
  exposed: number;   // usable but with no backup — the actionable number
}

const REDUNDANCY_OPTIONS = ["Provider backup", "Key backup", "No backup"] as const;

function redundancyOf(m: MyModel): (typeof REDUNDANCY_OPTIONS)[number] {
  if (m.has_backup_provider) return "Provider backup";
  if (m.has_backup_key) return "Key backup";
  return "No backup";
}

export default function Models() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["my-models"], queryFn: api.myModels });

  // ── filter state — all combinable ──
  const [mode, setMode] = useState<"chat" | "embedding">("chat");
  const [search, setSearch] = useState("");
  const [providerSel, setProviderSel] = useState<Set<string>>(new Set());
  const [publisherSel, setPublisherSel] = useState<Set<string>>(new Set());
  // Live pre-ticked = the old "hide unavailable by default" behavior, but now
  // it's just a filter the user can see and change like any other.
  const [statusSel, setStatusSel] = useState<Set<string>>(new Set(["Live"]));
  const [redundancySel, setRedundancySel] = useState<Set<string>>(new Set());

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

  // Dropdown option counts are computed over the current MODE's models so the
  // numbers match what ticking the option can actually surface.
  const modeModels = mode === "embedding" ? embModels : chatModels;

  const providerOptions = useMemo(() => {
    const by = new Map<string, number>();
    for (const m of modeModels) for (const p of m.providers) by.set(p, (by.get(p) ?? 0) + 1);
    return [...by.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([value, count]) => ({ value, count }));
  }, [modeModels]);

  const publisherOptions = useMemo(() => {
    const by = new Map<string, number>();
    for (const m of modeModels) {
      const pub = m.publisher ?? "Unknown";
      by.set(pub, (by.get(pub) ?? 0) + 1);
    }
    return [...by.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([value, count]) => ({ value, count }));
  }, [modeModels]);

  // Status options are the classified REASONS (Live, Auth failed, Rate
  // limited, No access, Check failed…) — only ones that actually occur.
  const statusOptions = useMemo(() => {
    const by = new Map<string, number>();
    for (const m of modeModels) {
      const { label } = statusInfo(m);
      by.set(label, (by.get(label) ?? 0) + 1);
    }
    // Live first, then by count.
    return [...by.entries()]
      .sort((a, b) => (a[0] === "Live" ? -1 : b[0] === "Live" ? 1 : b[1] - a[1]))
      .map(([value, count]) => ({ value, count }));
  }, [modeModels]);

  const redundancyOptions = useMemo(() =>
    REDUNDANCY_OPTIONS.map((value) => ({
      value,
      count: modeModels.filter((m) => redundancyOf(m) === value).length,
    })), [modeModels]);

  // ── apply every filter: OR inside a dropdown, AND across dropdowns ──
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return modeModels.filter((m) => {
      if (providerSel.size > 0 && !m.providers.some((p) => providerSel.has(p))) return false;
      if (publisherSel.size > 0 && !publisherSel.has(m.publisher ?? "Unknown")) return false;
      if (statusSel.size > 0 && !statusSel.has(statusInfo(m).label)) return false;
      if (redundancySel.size > 0 && !redundancySel.has(redundancyOf(m))) return false;
      if (q && !m.model.toLowerCase().includes(q) &&
          !(m.publisher ?? "").toLowerCase().includes(q)) return false;
      return true;
    });
  }, [modeModels, providerSel, publisherSel, statusSel, redundancySel, search]);

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

  // "Live" alone is the default state, not a user filter worth flagging.
  const defaultStatus = statusSel.size === 1 && statusSel.has("Live");
  const anyFilter =
    search.trim() !== "" ||
    providerSel.size > 0 ||
    publisherSel.size > 0 ||
    redundancySel.size > 0 ||
    !defaultStatus;

  function clearFilters() {
    setSearch("");
    setProviderSel(new Set());
    setPublisherSel(new Set());
    setStatusSel(new Set(["Live"]));
    setRedundancySel(new Set());
  }

  // Cards toggle the Provider filter (and always mean chat mode).
  function pickCard(name: string) {
    setMode("chat");
    setProviderSel((cur) => {
      const next = new Set(cur);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  const tableTitle =
    `${shown.length} ${mode === "embedding" ? "embedding" : "chat"} model${shown.length === 1 ? "" : "s"}` +
    (anyFilter ? " (filtered)" : "");

  return (
    <div className="min-w-0 space-y-6">
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

      {/* ── cards: providers (chat) + embeddings — shortcuts into the filters ── */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {groups.map((g) => {
          const isSel = mode === "chat" && providerSel.has(g.name);
          return (
            <button
              key={g.name}
              onClick={() => pickCard(g.name)}
              className={`card text-left transition-colors ${
                isSel ? "border-emerald-700 bg-emerald-950/20" : "hover:border-neutral-700"
              }`}
            >
              <div className="flex items-center justify-between">
                <h2 className="font-medium text-neutral-100">{g.name}</h2>
                <span className={`text-xs ${isSel ? "text-emerald-400" : "text-neutral-600"}`}>
                  {isSel ? "filtering ▾" : "click to filter"}
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

        {/* Embeddings are a different surface (/v1/embeddings), hence a mode
            switch on the table rather than rows mixed into chat counts. */}
        {embModels.length > 0 && (
          <button
            onClick={() => setMode((m) => (m === "embedding" ? "chat" : "embedding"))}
            className={`card text-left transition-colors ${
              mode === "embedding" ? "border-purple-700 bg-purple-950/20" : "hover:border-neutral-700"
            }`}
          >
            <div className="flex items-center justify-between">
              <h2 className="font-medium text-purple-300">Embeddings</h2>
              <span className={`text-xs ${mode === "embedding" ? "text-purple-400" : "text-neutral-600"}`}>
                {mode === "embedding" ? "showing ▾" : "click to view"}
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

      {/* ── filter bar: everything combines ── */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search models…"
          className="w-56 rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-1.5 text-sm
                     text-neutral-200 outline-none placeholder:text-neutral-600
                     focus:border-neutral-600"
        />
        <FilterDropdown
          label="Provider" options={providerOptions}
          selected={providerSel} onChange={setProviderSel}
        />
        <FilterDropdown
          label="Publisher" options={publisherOptions}
          selected={publisherSel} onChange={setPublisherSel}
        />
        <FilterDropdown
          label="Status" options={statusOptions}
          selected={statusSel} onChange={setStatusSel}
        />
        <FilterDropdown
          label="Redundancy" options={redundancyOptions}
          selected={redundancySel} onChange={setRedundancySel}
        />
        {anyFilter && (
          <button
            className="text-xs text-neutral-500 hover:text-neutral-300"
            onClick={clearFilters}
          >
            clear filters ✕
          </button>
        )}
      </div>

      {/* ── the model table ── */}
      <div className="card p-0">
        <div className="border-b border-neutral-800 px-4 py-3">
          <h3 className="text-sm font-medium text-neutral-300">{tableTitle}</h3>
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
              <th className="th"></th>
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && (
              <tr>
                <td className="td text-neutral-600" colSpan={7}>
                  No models match these filters —{" "}
                  <button className="underline hover:text-neutral-300" onClick={clearFilters}>
                    clear filters
                  </button>
                  .
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
                  {(() => {
                    const info = statusInfo(m);
                    return (
                      <span className="inline-flex items-center gap-2" title={info.hint}>
                        <span className={`inline-block h-2 w-2 rounded-full ${info.dot}`} />
                        <span className={info.text}>{info.label}</span>
                      </span>
                    );
                  })()}
                </td>
                <td className="td text-right">
                  {/* Embedding models can't chat, and a dead model can't answer —
                      only live chat rows get the shortcut. */}
                  {m.mode !== "embedding" && m.is_usable && (
                    <Link
                      to={`/playground?model=${encodeURIComponent(m.model)}`}
                      className="rounded-lg border border-neutral-800 px-2.5 py-1 text-xs
                                 text-neutral-400 transition-colors hover:border-emerald-700
                                 hover:text-emerald-300"
                      title="Open the Playground with this model selected"
                    >
                      Try ↗
                    </Link>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
