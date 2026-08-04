"use client";

/**
 * Health timeline — SRS §20.
 *
 * `deployments.status` is overwritten in place, so once a throttled key
 * recovers there is nothing left to say it was ever throttled. This screen reads
 * deployment_status_events, an append-only table that records TRANSITIONS ONLY
 * (backend/app/services/health_history.py).
 *
 * That "transitions only" rule is why a flat list is readable here: the re-probe
 * loop sweeps every unhealthy deployment every 20 minutes (SRS §10), and if each
 * sweep wrote a row this page would be thousands of identical lines. Every row
 * below is a real change of state.
 *
 * History begins at the deploy that introduced the table — there is no backfill,
 * because the data to backfill from was overwritten. An empty timeline means
 * "nothing has changed since then", not "nothing is being recorded".
 */

import { useState } from "react";

import { useGateway } from "@/shared/gateway/useGateway";
import {
  EmptyRow,
  ErrorRow,
  LoadingRow,
  PageHeader,
  Panel,
  StatusPill,
  relativeTime,
} from "@/shared/gateway/primitives";

type Event = {
  id: number;
  at: string;
  deployment_id: number;
  provider: string;
  key_masked: string;
  model: string;
  from_status: string | null;
  to_status: string;
  source: string;
  http_code: number | null;
  error: string | null;
  recovered: boolean;
};

type Timeline = { window_days: number; total: number; events: Event[] };

export default function HealthTimelinePage() {
  const [days, setDays] = useState(7);
  const { data, loading, error, refresh } = useGateway<Timeline>(
    `/api/health/timeline?days=${days}&limit=300`,
    { pollMs: 30000 }
  );

  return (
    <div className="p-6">
      <PageHeader
        title="Health timeline"
        srs="§20"
        description="When a key got throttled, when its cooldown expired, when a probe revived it. Transitions only — a status that did not change is not an event."
        actions={
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="rounded-lg border px-2 py-1 text-sm"
            style={{
              background: "var(--color-bg-subtle)",
              borderColor: "var(--color-border)",
              color: "var(--color-text-main)",
            }}
          >
            <option value={1}>Last 24 hours</option>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
          </select>
        }
      />

      <Panel title="Status changes" subtitle={data ? `${data.total} in the window` : undefined}>
        {loading && !data ? (
          <LoadingRow label="Loading timeline…" />
        ) : error ? (
          <ErrorRow message={error} onRetry={refresh} />
        ) : !data?.events.length ? (
          <EmptyRow
            title="No status changes recorded"
            hint="Recording starts when a deployment's health actually changes — nothing is backfilled, because the previous status was overwritten in place. A quiet gateway produces an empty timeline."
          />
        ) : (
          <ol className="divide-y" style={{ borderColor: "var(--color-border)" }}>
            {data.events.map((e) => (
              <li key={e.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <span
                  className="w-20 shrink-0 text-xs tabular-nums"
                  style={{ color: "var(--color-text-muted)" }}
                  title={new Date(e.at).toLocaleString()}
                >
                  {relativeTime(e.at)}
                </span>

                <span className="flex items-center gap-2">
                  {e.from_status ? (
                    <StatusPill status={e.from_status} />
                  ) : (
                    <span className="text-xs" style={{ color: "var(--color-text-muted)" }}>
                      first seen
                    </span>
                  )}
                  <span style={{ color: "var(--color-text-muted)" }}>→</span>
                  <StatusPill status={e.to_status} />
                </span>

                <span className="text-sm" style={{ color: "var(--color-text-main)" }}>
                  {e.provider}
                  <span style={{ color: "var(--color-text-muted)" }}> · </span>
                  {e.model}
                </span>

                <span className="font-mono text-xs" style={{ color: "var(--color-text-muted)" }}>
                  {e.key_masked}
                </span>

                {/* A probe means the gateway went looking; a request means a
                    user's call hit it. Different questions, so they are labelled. */}
                <span
                  className="rounded px-1.5 py-0.5 text-[10px]"
                  style={{
                    background: "var(--color-bg-subtle)",
                    color: "var(--color-text-muted)",
                  }}
                  title={
                    e.source === "probe"
                      ? "Observed by a background probe"
                      : "Observed from real traffic"
                  }
                >
                  {e.source}
                </span>

                {e.recovered && (
                  <span className="text-xs" style={{ color: "#22c55e" }}>
                    recovered
                  </span>
                )}

                {e.error && (
                  <span
                    className="max-w-xs truncate text-xs"
                    style={{ color: "var(--color-text-muted)" }}
                    title={e.error}
                  >
                    {e.error}
                  </span>
                )}
              </li>
            ))}
          </ol>
        )}
      </Panel>
    </div>
  );
}
