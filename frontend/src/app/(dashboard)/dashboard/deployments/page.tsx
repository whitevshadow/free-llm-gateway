"use client";

/**
 * Deployments — SRS §6.1.
 *
 * The SRS calls the deployment "the unit of everything": one (provider, key,
 * model) triple, and the thing health, cooldowns, probing and routing all
 * operate on. Nothing in the ported UI had this concept — OmniRoute reasons
 * about provider "connections" — so this screen is written from the spec rather
 * than adapted.
 *
 * The column that justifies the page is CALLABLE. `is_working` is a generated
 * column meaning status = 'available'; a rate-limited deployment whose cooldown
 * has expired is not available but IS callable again, which is exactly how a
 * 429'd free tier self-heals (SRS §7.3). A screen that showed `is_working`
 * would report recovered keys as dead, so both are shown and the difference is
 * made explicit.
 *
 * Reached from Providers by drilling in (?provider=<slug>), which is how the
 * navigation is meant to be read: connect a provider, then look at what it
 * actually gives you.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";

import { useGateway } from "@/shared/gateway/useGateway";
import {
  EmptyRow,
  ErrorRow,
  LoadingRow,
  PageHeader,
  Panel,
  StatTile,
  StatusPill,
  TableScroll,
  Td,
  Th,
  duration,
  relativeTime,
} from "@/shared/gateway/primitives";

type Deployment = {
  id: number;
  provider: string;
  provider_slug: string;
  key_id: number;
  key_label: string | null;
  key_masked: string;
  model: string;
  upstream_model_id: string;
  mode: string | null;
  status: string;
  is_working: boolean;
  is_cooling_down: boolean;
  is_callable: boolean;
  http_code: number | null;
  latency_ms: number | null;
  error: string | null;
  cooldown_seconds_left: number;
  rate_limit_strikes: number;
  last_checked_at: string | null;
  last_used_at: string | null;
};

type Response = {
  total: number;
  limit: number;
  offset: number;
  deployments: Deployment[];
};

const STATUSES = [
  "available",
  "rate_limited",
  "auth_error",
  "timeout",
  "unavailable",
  "error",
] as const;

type ProbeStatus = { active: boolean; total: number; done: number };

/**
 * Start a real probe and follow it to completion.
 *
 * "Refresh" used to be a re-FETCH: it re-read the same stored health rows and
 * redrew them, so a screen whose "Checked" column said 12 days ago said 12 days
 * ago afterwards too. Nothing on this page ever asked a provider whether a
 * deployment still answers. This does — POST starts the probe, then
 * /api/probe-status is polled until the run drains.
 *
 * `provider` is threaded through so a filtered view probes only what it shows.
 * Re-testing 245 NVIDIA deployments to answer a question about Cohere would
 * spend a free tier's quota on models the operator is not looking at.
 */
function useProbeRun(onFinished: () => void) {
  const [status, setStatus] = useState<ProbeStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  // Polling is driven by this flag rather than by `status.active`, so the very
  // first tick — before any status has arrived — still schedules a poll.
  const [watching, setWatching] = useState(false);

  useEffect(() => {
    if (!watching) return;
    let cancelled = false;

    const tick = async () => {
      try {
        const res = await fetch("/api/probe-status", {
          headers: { accept: "application/json" },
        });
        const body = (await res.json()) as Partial<ProbeStatus>;
        if (cancelled) return;
        const next: ProbeStatus = {
          active: Boolean(body.active),
          total: Number(body.total ?? 0),
          done: Number(body.done ?? 0),
        };
        setStatus(next);
        if (!next.active) {
          setWatching(false);
          // Results landed in the database as each probe returned; pull them.
          finishedRef.current();
        }
      } catch {
        // A dropped poll is not a failed probe — the run continues server-side.
        // Keep watching rather than reporting an error the operator can't act on.
      }
    };

    void tick();
    const id = setInterval(tick, 1500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [watching]);

  const start = useCallback(async (provider: string) => {
    setError(null);
    setStarting(true);
    try {
      const res = await fetch("/api/models/test-all", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(provider ? { provider } : {}),
      });
      const body = (await res.json()) as { error?: string; detail?: string };
      if (!res.ok) {
        setError(body.detail ?? body.error ?? "Could not start the probe.");
        return;
      }
      // Probing is a background job, so the POST returns before any result
      // exists. Watching starts here; the bar fills as probes land.
      setStatus({ active: true, total: 0, done: 0 });
      setWatching(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the probe.");
    } finally {
      setStarting(false);
    }
  }, []);

  return { start, status, starting, error, running: watching || starting };
}

export default function DeploymentsPage() {
  const params = useSearchParams();
  // Deep-linked from the provider list, so drilling in lands pre-filtered.
  const providerFromUrl = params.get("provider") ?? "";

  const [status, setStatus] = useState("");
  const [provider, setProvider] = useState(providerFromUrl);
  const [search, setSearch] = useState("");

  const query = useMemo(() => {
    const q = new URLSearchParams({ limit: "1000" });
    if (status) q.set("status", status);
    if (provider) q.set("provider", provider);
    return `/api/deployments?${q.toString()}`;
  }, [status, provider]);

  // Polled: cooldowns tick down and probes land while the page is open, so a
  // static table would show a benched key that recovered a minute ago.
  const { data, loading, error, refresh } = useGateway<Response>(query, { pollMs: 15000 });

  const probe = useProbeRun(refresh);

  const rows = useMemo(() => {
    const all = data?.deployments ?? [];
    if (!search.trim()) return all;
    const needle = search.trim().toLowerCase();
    // Client-side because the filter is a substring over an already-loaded page;
    // a round trip per keystroke would be slower and no more correct.
    return all.filter(
      (d) =>
        d.model.toLowerCase().includes(needle) ||
        d.provider.toLowerCase().includes(needle) ||
        (d.key_label ?? "").toLowerCase().includes(needle)
    );
  }, [data, search]);

  const stats = useMemo(() => {
    const all = data?.deployments ?? [];
    return {
      total: data?.total ?? 0,
      callable: all.filter((d) => d.is_callable).length,
      cooling: all.filter((d) => d.is_cooling_down).length,
      dead: all.filter((d) => d.status === "auth_error").length,
    };
  }, [data]);

  const providers = useMemo(() => {
    const seen = new Map<string, string>();
    for (const d of data?.deployments ?? []) seen.set(d.provider_slug, d.provider);
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [data]);

  const selectStyle = {
    background: "var(--color-bg-subtle)",
    borderColor: "var(--color-border)",
    color: "var(--color-text-main)",
  };

  return (
    <div className="p-6">
      <PageHeader
        title="Deployments"
        srs="§6.1"
        description="One row per (provider, key, model). Health, cooldowns and routing all operate on this unit — not on providers or models in the abstract."
        actions={
          <div className="flex items-center gap-2">
            {/* Reload is the old Refresh: redraw stored health, ask nobody.
                Kept because it is the cheap answer to "did the background probe
                land yet?" and costs no provider quota. */}
            <button
              onClick={refresh}
              className="rounded-lg border px-3 py-1.5 text-sm"
              style={{ borderColor: "var(--color-border)", color: "var(--color-text-main)" }}
            >
              Reload
            </button>
            <button
              onClick={() => probe.start(provider)}
              disabled={probe.running}
              className="rounded-lg border px-3 py-1.5 text-sm disabled:opacity-60"
              style={{ borderColor: "var(--color-border)", color: "var(--color-text-main)" }}
              title={
                provider
                  ? `Re-test every ${provider} deployment against the provider.`
                  : "Re-test every deployment — each model, on each of your keys."
              }
            >
              {probe.running ? "Testing…" : provider ? "Re-test provider" : "Re-test all"}
            </button>
          </div>
        }
      />

      {(probe.running || probe.error) && (
        <div
          className="mb-4 rounded-lg border px-3 py-2 text-sm"
          style={{ borderColor: "var(--color-border)", color: "var(--color-text-muted)" }}
        >
          {probe.error ? (
            <span style={{ color: "#ef4444" }}>{probe.error}</span>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <span>
                  Testing {provider || "every"} deployment against the provider —
                  results land row by row.
                </span>
                <span>
                  {probe.status?.total
                    ? `${probe.status.done} / ${probe.status.total}`
                    : "starting…"}
                </span>
              </div>
              <div
                className="mt-2 h-1 w-full overflow-hidden rounded"
                style={{ background: "var(--color-bg-subtle)" }}
              >
                <div
                  className="h-full transition-all"
                  style={{
                    background: "#22c55e",
                    width: probe.status?.total
                      ? `${Math.min(100, (probe.status.done / probe.status.total) * 100)}%`
                      : "0%",
                  }}
                />
              </div>
            </>
          )}
        </div>
      )}

      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile label="Deployments" value={stats.total} hint="provider × key × model" />
        <StatTile label="Callable now" value={stats.callable} tone="good" hint="router can pick these" />
        <StatTile
          label="Cooling down"
          value={stats.cooling}
          tone={stats.cooling ? "warn" : "default"}
          hint="throttled, revives on its own"
        />
        <StatTile
          label="Dead keys"
          value={stats.dead}
          tone={stats.dead ? "bad" : "default"}
          hint="auth_error — never auto-recovers"
        />
      </div>

      <Panel
        title="All deployments"
        subtitle={`${rows.length} shown${data ? ` of ${data.total}` : ""}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter model, provider, key…"
              className="rounded-lg border px-2 py-1 text-xs"
              style={selectStyle}
            />
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              className="rounded-lg border px-2 py-1 text-xs"
              style={selectStyle}
            >
              <option value="">All providers</option>
              {providers.map(([slug, name]) => (
                <option key={slug} value={slug}>
                  {name}
                </option>
              ))}
            </select>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className="rounded-lg border px-2 py-1 text-xs"
              style={selectStyle}
            >
              <option value="">All statuses</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        }
      >
        {loading && !data ? (
          <LoadingRow label="Loading deployments…" />
        ) : error ? (
          <ErrorRow message={error} onRetry={refresh} />
        ) : rows.length === 0 ? (
          <EmptyRow
            title="No deployments match"
            hint={
              provider || status || search
                ? "Try clearing the filters."
                : "Add a provider key on Providers & keys — deployments are created for every enabled model of that provider, then promoted by a background probe."
            }
          />
        ) : (
          <TableScroll>
            <table className="w-full">
              <thead>
                <tr style={{ background: "var(--table-header-bg)" }}>
                  <Th>Provider</Th>
                  <Th>Key</Th>
                  <Th>Model</Th>
                  <Th>Status</Th>
                  <Th>Callable</Th>
                  <Th right>Latency</Th>
                  <Th right>HTTP</Th>
                  <Th right>Cooldown</Th>
                  <Th right>429s</Th>
                  <Th right>Checked</Th>
                  <Th right>Last used</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((d) => (
                  <tr
                    key={d.id}
                    className="border-t"
                    style={{ borderColor: "var(--table-cell-border)" }}
                  >
                    <Td>
                      <Link
                        href={`/dashboard/deployments?provider=${d.provider_slug}`}
                        className="hover:underline"
                      >
                        {d.provider}
                      </Link>
                    </Td>
                    <Td mono>
                      <span title={d.key_label ?? undefined}>{d.key_masked}</span>
                    </Td>
                    <Td>
                      <span title={`upstream: ${d.upstream_model_id}`}>{d.model}</span>
                      {d.mode && d.mode !== "chat" && (
                        <span
                          className="ml-2 rounded px-1 text-[10px]"
                          style={{
                            background: "var(--color-bg-subtle)",
                            color: "var(--color-text-muted)",
                          }}
                        >
                          {d.mode}
                        </span>
                      )}
                    </Td>
                    <Td>
                      <StatusPill status={d.status} title={d.error ?? undefined} />
                    </Td>
                    <Td>
                      {/* The distinction the SRS cares about: benched now vs
                          genuinely unusable. */}
                      {d.is_callable ? (
                        <span style={{ color: "#22c55e" }}>yes</span>
                      ) : d.is_cooling_down ? (
                        <span style={{ color: "#f59e0b" }} title="Cooling down — revives on expiry">
                          waiting
                        </span>
                      ) : (
                        <span style={{ color: "var(--color-text-muted)" }}>no</span>
                      )}
                    </Td>
                    <Td right>{d.latency_ms ? `${d.latency_ms}ms` : "—"}</Td>
                    <Td right>{d.http_code ?? "—"}</Td>
                    <Td right>{duration(d.cooldown_seconds_left)}</Td>
                    <Td right>
                      {d.rate_limit_strikes ? (
                        <span
                          style={{ color: d.rate_limit_strikes >= 3 ? "#ef4444" : "#f59e0b" }}
                          title="Consecutive 429s — drives the 60s → 2m → 5m cooldown ladder (SRS §7.3)"
                        >
                          {d.rate_limit_strikes}
                        </span>
                      ) : (
                        "—"
                      )}
                    </Td>
                    <Td right>{relativeTime(d.last_checked_at)}</Td>
                    <Td right>{relativeTime(d.last_used_at)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </Panel>
    </div>
  );
}
