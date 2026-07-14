/**
 * Dashboard — top 5 models by token usage, top providers, and per-key burn.
 *
 * EMPTY STATE FIRST. A user with no keys has no traffic, so charts would render
 * as blank rectangles with axes — which looks broken rather than new. If they
 * have no keys we skip straight to the onboarding CTA.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip,
  XAxis, YAxis, Area, AreaChart,
} from "recharts";
import { useState } from "react";

import { api } from "../lib/api";
import type { Me } from "../lib/types";
import { Empty, Spinner, StatCard } from "../components/ui";

const COLORS = ["#10b981", "#3b82f6", "#a855f7", "#f59e0b", "#ef4444"];

const tooltipStyle = {
  contentStyle: {
    background: "#0a0a0a",
    border: "1px solid #262626",
    borderRadius: 8,
    fontSize: 12,
  },
} as const;

export default function Dashboard({ me }: { me: Me }) {
  const [days, setDays] = useState(30);
  const { data, isLoading } = useQuery({
    queryKey: ["usage", days],
    queryFn: () => api.usage(days),
  });

  // No keys => no models => no traffic. Onboard instead of showing empty charts.
  if (me.provider_key_count === 0) {
    return (
      <Empty
        title="Add your first provider key"
        body="The gateway calls LLM providers using your own API keys. Add one and it
              fans out into every model that provider serves — each key tested
              separately, so an exhausted free tier is benched while its siblings
              keep serving."
        action={<Link to="/providers" className="btn-pri">Add a provider key</Link>}
      />
    );
  }

  if (isLoading || !data) return <Spinner label="Loading usage…" />;

  const { totals, top_models, top_providers, daily, per_key } = data;

  if (totals.requests === 0) {
    return (
      <Empty
        title="No requests yet"
        body="Your keys are set up, but nothing has called the gateway. Point a client
              at /v1/chat/completions with your gateway key, or try the Playground."
        action={<Link to="/playground" className="btn-pri">Open the Playground</Link>}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-neutral-100">Dashboard</h1>
        <div className="flex gap-1">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`btn px-2.5 py-1 text-xs ${
                days === d
                  ? "bg-neutral-800 text-neutral-100"
                  : "text-neutral-500 hover:text-neutral-300"
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* ── totals ── */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Requests" value={totals.requests} />
        <StatCard
          label="Tokens"
          value={totals.total_tokens}
          sub={`${totals.prompt_tokens.toLocaleString()} in · ${totals.completion_tokens.toLocaleString()} out`}
        />
        <StatCard label="Avg latency" value={`${totals.avg_latency_ms} ms`} />
        <StatCard
          label="Errors"
          value={totals.errors}
          tone={totals.errors > 0 ? "warn" : "default"}
          sub={totals.errors > 0 ? "includes rate limits that fell back" : undefined}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* ── top 5 models ── */}
        <div className="card">
          <h2 className="mb-4 text-sm font-medium text-neutral-300">
            Top models by tokens
          </h2>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={top_models} layout="vertical" margin={{ left: 8 }}>
              <XAxis type="number" hide />
              <YAxis
                type="category"
                dataKey="model"
                width={130}
                tick={{ fill: "#a3a3a3", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
                {...tooltipStyle}
                formatter={(v: number) => [v.toLocaleString(), "tokens"]}
              />
              <Bar dataKey="tokens" radius={[0, 4, 4, 0]}>
                {top_models.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* ── top providers ── */}
        <div className="card">
          <h2 className="mb-4 text-sm font-medium text-neutral-300">Top providers</h2>
          <div className="flex items-center gap-6">
            <ResponsiveContainer width="55%" height={240}>
              <PieChart>
                <Pie
                  data={top_providers}
                  dataKey="tokens"
                  nameKey="provider"
                  innerRadius={55}
                  outerRadius={90}
                  paddingAngle={2}
                >
                  {top_providers.map((_, i) => (
                    <Cell key={i} fill={COLORS[i % COLORS.length]} stroke="none" />
                  ))}
                </Pie>
                <Tooltip
                  {...tooltipStyle}
                  formatter={(v: number) => [v.toLocaleString(), "tokens"]}
                />
              </PieChart>
            </ResponsiveContainer>
            <ul className="flex-1 space-y-2.5">
              {top_providers.map((p, i) => (
                <li key={p.slug} className="flex items-center gap-2 text-sm">
                  <span
                    className="h-2.5 w-2.5 rounded-sm"
                    style={{ background: COLORS[i % COLORS.length] }}
                  />
                  <span className="flex-1 text-neutral-300">{p.provider}</span>
                  <span className="tabular-nums text-neutral-500">
                    {p.requests.toLocaleString()} req
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      {/* ── tokens over time ── */}
      <div className="card">
        <h2 className="mb-4 text-sm font-medium text-neutral-300">Tokens over time</h2>
        <ResponsiveContainer width="100%" height={180}>
          <AreaChart data={daily}>
            <defs>
              <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#10b981" stopOpacity={0.35} />
                <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="date"
              tick={{ fill: "#525252", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: "#525252", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={45}
            />
            <Tooltip {...tooltipStyle} />
            <Area
              type="monotone"
              dataKey="tokens"
              stroke="#10b981"
              strokeWidth={2}
              fill="url(#g)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* ── per-key burn ──
          This table is the reason the multi-key design exists. It shows which of
          your keys is carrying the load and which has nothing left. */}
      <div className="card">
        <h2 className="mb-1 text-sm font-medium text-neutral-300">Usage by key</h2>
        <p className="mb-4 text-xs text-neutral-600">
          Each key is an independent free-tier budget. When one is rate-limited,
          the others keep serving.
        </p>
        <table className="w-full">
          <thead>
            <tr>
              <th className="th">Key</th>
              <th className="th">Provider</th>
              <th className="th text-right">Requests</th>
              <th className="th text-right">Tokens</th>
              <th className="th text-right">Live models</th>
            </tr>
          </thead>
          <tbody>
            {per_key.map((k) => (
              <tr key={k.id}>
                <td className="td">
                  <span className="text-neutral-200">{k.label}</span>{" "}
                  <span className="font-mono text-xs text-neutral-600">{k.masked}</span>
                </td>
                <td className="td text-neutral-400">{k.provider}</td>
                <td className="td text-right tabular-nums">{k.requests.toLocaleString()}</td>
                <td className="td text-right tabular-nums">{k.tokens.toLocaleString()}</td>
                <td className="td text-right tabular-nums">
                  <span className={k.working_models === 0 ? "text-amber-400" : ""}>
                    {k.working_models}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
