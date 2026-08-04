"use client";

/**
 * Model registry — SRS §12.
 *
 * The public model ID is the NORMALIZED name, not any provider's model string
 * (SRS §12, §16): provider ids are ephemeral deployment details, so if a
 * provider renames a model the name you call does not change. Deployments
 * sharing a normalized name are interchangeable, which is what lets the router
 * fail over between providers under one public name.
 *
 * The three badges are the ones that answer "will this keep working?":
 *   backup key       — 2+ live deployments; if one fails there IS another,
 *                      including two keys at the same provider (SRS §6.1).
 *   backup provider  — stronger: 2+ live PROVIDERS, so it survives an outage.
 *   statuses         — WHY the dead ones are dead, so an unusable model reads as
 *                      "all keys rate limited" rather than a flat "unavailable".
 */

import { useMemo, useState } from "react";
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
  relativeTime,
} from "@/shared/gateway/primitives";

type Model = {
  provider: string;
  model: string;
  available: boolean;
  mode: string | null;
  publisher: string | null;
  providers: string[];
  statuses: string[];
  liveKeys: number;
  totalKeys: number;
  hasBackupKey: boolean;
  hasBackupProvider: boolean;
  lastCheckedAt: string | null;
};

type Response = { models: Model[] };

export default function ModelsPage() {
  const [search, setSearch] = useState("");
  const [usableOnly, setUsableOnly] = useState(false);

  const { data, loading, error, refresh } = useGateway<Response>("/api/models");

  const rows = useMemo(() => {
    let all = data?.models ?? [];
    if (usableOnly) all = all.filter((m) => m.available);
    if (search.trim()) {
      const needle = search.trim().toLowerCase();
      all = all.filter(
        (m) =>
          m.model.toLowerCase().includes(needle) ||
          (m.publisher ?? "").toLowerCase().includes(needle) ||
          m.providers.some((p) => p.toLowerCase().includes(needle))
      );
    }
    return all;
  }, [data, search, usableOnly]);

  const stats = useMemo(() => {
    const all = data?.models ?? [];
    return {
      total: all.length,
      usable: all.filter((m) => m.available).length,
      redundant: all.filter((m) => m.hasBackupProvider).length,
    };
  }, [data]);

  const controlStyle = {
    background: "var(--color-bg-subtle)",
    borderColor: "var(--color-border)",
    color: "var(--color-text-main)",
  };

  return (
    <div className="p-6">
      <PageHeader
        title="Models"
        srs="§12"
        description="What you can call right now, by stable normalized name. Served from the gateway's own database — a provider's /models endpoint is never called during a request."
        actions={
          <button
            onClick={refresh}
            className="rounded-lg border px-3 py-1.5 text-sm"
            style={{ borderColor: "var(--color-border)", color: "var(--color-text-main)" }}
          >
            Refresh
          </button>
        }
      />

      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3">
        <StatTile label="Models in catalog" value={stats.total} />
        <StatTile
          label="Callable now"
          value={stats.usable}
          tone={stats.usable ? "good" : "warn"}
          hint="at least one live deployment"
        />
        <StatTile
          label="Survives a provider outage"
          value={stats.redundant}
          hint="2+ live providers serve it"
        />
      </div>

      <Panel
        title="Registry"
        subtitle={`${rows.length} shown`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter model, publisher, provider…"
              className="rounded-lg border px-2 py-1 text-xs"
              style={controlStyle}
            />
            <label
              className="flex items-center gap-1.5 text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              <input
                type="checkbox"
                checked={usableOnly}
                onChange={(e) => setUsableOnly(e.target.checked)}
              />
              Callable only
            </label>
          </div>
        }
      >
        {loading && !data ? (
          <LoadingRow label="Loading the registry…" />
        ) : error ? (
          <ErrorRow message={error} onRetry={refresh} />
        ) : !rows.length ? (
          <EmptyRow
            title="No models"
            hint="Models appear once a provider key exists and discovery has run. Add a key on Providers & keys."
          />
        ) : (
          <TableScroll>
            <table className="w-full">
              <thead>
                <tr style={{ background: "var(--table-header-bg)" }}>
                  <Th>Model</Th>
                  <Th>Publisher</Th>
                  <Th>Mode</Th>
                  <Th>Served by</Th>
                  <Th right>Live keys</Th>
                  <Th>Redundancy</Th>
                  <Th>Health</Th>
                  <Th right>Checked</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => (
                  <tr
                    key={m.model}
                    className="border-t"
                    style={{ borderColor: "var(--table-cell-border)" }}
                  >
                    <Td>
                      <span style={{ color: m.available ? undefined : "var(--color-text-muted)" }}>
                        {m.model}
                      </span>
                    </Td>
                    <Td>{m.publisher ?? "—"}</Td>
                    <Td>{m.mode ?? "chat"}</Td>
                    <Td>
                      <Link
                        href={`/dashboard/deployments?search=${encodeURIComponent(m.model)}`}
                        className="hover:underline"
                        title={m.providers.join(", ")}
                      >
                        {m.providers.length === 1
                          ? m.providers[0]
                          : `${m.providers.length} providers`}
                      </Link>
                    </Td>
                    <Td right>
                      {m.liveKeys}/{m.totalKeys}
                    </Td>
                    <Td>
                      {m.hasBackupProvider ? (
                        <span style={{ color: "#22c55e" }} title="2+ live providers — survives a provider outage">
                          provider
                        </span>
                      ) : m.hasBackupKey ? (
                        <span style={{ color: "#f59e0b" }} title="2+ live keys, but all at one provider">
                          key only
                        </span>
                      ) : (
                        <span style={{ color: "var(--color-text-muted)" }} title="Single point of failure">
                          none
                        </span>
                      )}
                    </Td>
                    <Td>
                      <span className="flex flex-wrap gap-1">
                        {m.statuses.slice(0, 3).map((s) => (
                          <StatusPill key={s} status={s} />
                        ))}
                      </span>
                    </Td>
                    <Td right>{relativeTime(m.lastCheckedAt)}</Td>
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
