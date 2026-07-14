/**
 * Activity log — every call, and WHICH KEY answered it.
 *
 * The key column is the point. A 429 next to "Groq #1" followed by a 200 next to
 * "Groq #2" is the whole product working: one free-tier key hit its limit and its
 * sibling served the retry. Rendered as amber, not red — it is not a failure.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { Empty, Spinner } from "../components/ui";

type Filter = "all" | "ok" | "error";

export default function Logs() {
  const [filter, setFilter] = useState<Filter>("all");
  const { data, isLoading } = useQuery({
    queryKey: ["logs", filter],
    queryFn: () => api.logs(100, filter === "all" ? undefined : filter),
  });

  if (isLoading) return <Spinner />;
  const logs = data?.logs ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-neutral-100">Activity</h1>
          <p className="mt-1 text-sm text-neutral-500">
            {data?.total ?? 0} requests · each attributed to the key that answered.
          </p>
        </div>
        <div className="flex gap-1">
          {(["all", "ok", "error"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`btn px-2.5 py-1 text-xs capitalize ${
                filter === f
                  ? "bg-neutral-800 text-neutral-100"
                  : "text-neutral-500 hover:text-neutral-300"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {logs.length === 0 ? (
        <Empty title="Nothing here yet" body="Requests you make through the gateway will show up here." />
      ) : (
        <div className="card p-0">
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">Time</th>
                <th className="th">Model</th>
                <th className="th">Answered by</th>
                <th className="th text-right">Tokens</th>
                <th className="th text-right">Latency</th>
                <th className="th text-right">Status</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((l) => {
                const rateLimited = l.status_code === 429;
                const failed = l.status_code >= 400 && !rateLimited;
                return (
                  <tr key={l.id} className="hover:bg-neutral-900/40">
                    <td className="td text-xs text-neutral-500">
                      {l.created_at ? new Date(l.created_at).toLocaleTimeString() : "—"}
                    </td>
                    <td className="td font-mono text-xs text-neutral-300">{l.model}</td>
                    <td className="td text-sm">
                      {l.key_label ? (
                        <>
                          <span className="text-neutral-300">{l.provider}</span>{" "}
                          <span className="text-neutral-600">· {l.key_label}</span>
                        </>
                      ) : (
                        <span className="text-neutral-700">—</span>
                      )}
                    </td>
                    <td className="td text-right tabular-nums text-neutral-400">
                      {l.total_tokens ? l.total_tokens.toLocaleString() : "—"}
                    </td>
                    <td className="td text-right tabular-nums text-neutral-500">
                      {l.latency_ms ? `${l.latency_ms} ms` : "—"}
                    </td>
                    <td className="td text-right">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs tabular-nums ${
                          rateLimited
                            ? "bg-amber-950/50 text-amber-400"
                            : failed
                            ? "bg-red-950/50 text-red-400"
                            : "bg-emerald-950/50 text-emerald-400"
                        }`}
                        title={rateLimited ? "Rate limited — this key was benched and another served the retry." : l.error ?? ""}
                      >
                        {l.status_code}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
