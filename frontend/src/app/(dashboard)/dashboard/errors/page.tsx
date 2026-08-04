"use client";

/**
 * Error feed — SRS §20.
 *
 * The point of this screen over the raw request log is the REASON column. The
 * gateway classifies failures into the vocabulary the prober and router share
 * (SRS §13.2), and the distinctions are operationally load-bearing:
 * `rate_limited` means the key is fine and throttled, `auth_error` means it is
 * dead and no timer will revive it. A feed of bare 429s and 401s makes the
 * reader do that translation every time.
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
  TableScroll,
  Td,
  Th,
  relativeTime,
} from "@/shared/gateway/primitives";

type ErrorRowData = {
  id: number;
  at: string;
  model: string | null;
  provider: string | null;
  key_masked: string | null;
  status_code: number | null;
  reason: string;
  message: string | null;
  latency_ms: number | null;
};

type Feed = {
  window_days: number;
  total: number;
  by_reason: Record<string, number>;
  errors: ErrorRowData[];
};

export default function ErrorFeedPage() {
  const [days, setDays] = useState(7);
  const { data, loading, error, refresh } = useGateway<Feed>(
    `/api/errors?days=${days}&limit=200`,
    { pollMs: 30000 }
  );

  return (
    <div className="p-6">
      <PageHeader
        title="Error feed"
        srs="§20"
        description="Recent failures, newest first, with the deployment that produced each and why it failed."
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

      {data && Object.keys(data.by_reason).length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {Object.entries(data.by_reason).map(([reason, count]) => (
            <div
              key={reason}
              className="flex items-center gap-2 rounded-lg border px-3 py-1.5"
              style={{ borderColor: "var(--color-border)", background: "var(--color-surface)" }}
            >
              <StatusPill status={reason} />
              <span className="text-sm tabular-nums" style={{ color: "var(--color-text-main)" }}>
                {count}
              </span>
            </div>
          ))}
        </div>
      )}

      <Panel title="Failures" subtitle={data ? `${data.total} in the window` : undefined}>
        {loading && !data ? (
          <LoadingRow label="Loading failures…" />
        ) : error ? (
          <ErrorRow message={error} onRetry={refresh} />
        ) : !data?.errors.length ? (
          <EmptyRow
            title="No failures in this window"
            hint="Nothing has returned a 4xx or 5xx. Widen the window if you expected to see something."
          />
        ) : (
          <TableScroll>
            <table className="w-full">
              <thead>
                <tr style={{ background: "var(--table-header-bg)" }}>
                  <Th>When</Th>
                  <Th>Reason</Th>
                  <Th right>Code</Th>
                  <Th>Model</Th>
                  <Th>Provider</Th>
                  <Th>Key</Th>
                  <Th right>Latency</Th>
                  <Th>Message</Th>
                </tr>
              </thead>
              <tbody>
                {data.errors.map((e) => (
                  <tr key={e.id} className="border-t" style={{ borderColor: "var(--table-cell-border)" }}>
                    <Td>{relativeTime(e.at)}</Td>
                    <Td><StatusPill status={e.reason} /></Td>
                    <Td right>{e.status_code ?? "—"}</Td>
                    <Td>{e.model ?? "—"}</Td>
                    <Td>{e.provider ?? "—"}</Td>
                    <Td mono>{e.key_masked ?? "—"}</Td>
                    <Td right>{e.latency_ms ? `${e.latency_ms}ms` : "—"}</Td>
                    <Td>
                      <span
                        className="block max-w-md truncate"
                        title={e.message ?? undefined}
                        style={{ color: "var(--color-text-muted)" }}
                      >
                        {e.message ?? "—"}
                      </span>
                    </Td>
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
