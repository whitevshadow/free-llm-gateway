/**
 * Router settings — the litellm.Router knobs for THIS user.
 *
 * These control how the router BEHAVES, not what it can call. The model list
 * comes from your deployments; to change it, add or remove a provider key.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "../lib/api";
import type { RouterConfig } from "../lib/types";
import { Spinner } from "../components/ui";

const STRATEGIES = [
  ["usage-based-routing-v2", "Spread load by recent usage (default)"],
  ["simple-shuffle", "Pick at random"],
  ["least-busy", "Fewest requests in flight"],
  ["usage-based-routing", "Usage-based (v1)"],
  ["latency-based-routing", "Fastest to respond"],
];

export default function Settings() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["router-config"], queryFn: api.routerConfig });
  const [form, setForm] = useState<RouterConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => { if (data) setForm(data); }, [data]);

  const save = useMutation({
    mutationFn: (body: Partial<RouterConfig>) => api.updateRouterConfig(body),
    onSuccess: () => {
      setSaved(true);
      setError(null);
      setTimeout(() => setSaved(false), 2500);
      qc.invalidateQueries({ queryKey: ["router-config"] });
    },
    onError: (e: ApiError) => setError(e.message),
  });

  const reset = useMutation({
    mutationFn: api.resetRouterConfig,
    onSuccess: (d) => {
      setForm(d);
      qc.invalidateQueries({ queryKey: ["router-config"] });
    },
  });

  if (isLoading || !form) return <Spinner />;

  const field = (k: keyof RouterConfig, v: number) => setForm({ ...form, [k]: v });

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-neutral-100">Router settings</h1>
        <p className="mt-1 text-sm text-neutral-500">
          How your router behaves when a call fails. It takes effect on your next request.
        </p>
      </div>

      <div className="card space-y-5">
        <div>
          <label className="label">Routing strategy</label>
          <select
            className="input"
            value={form.routing_strategy}
            onChange={(e) => setForm({ ...form, routing_strategy: e.target.value })}
          >
            {STRATEGIES.map(([v, d]) => (
              <option key={v} value={v}>{d}</option>
            ))}
          </select>
          <p className="mt-1.5 text-xs text-neutral-600">
            How to choose between your live deployments of the same model.
          </p>
        </div>

        <div className="grid gap-5 sm:grid-cols-3">
          <div>
            <label className="label">Retries</label>
            <input
              type="number" min={0} max={10} className="input"
              value={form.num_retries}
              onChange={(e) => field("num_retries", +e.target.value)}
            />
            <p className="mt-1.5 text-xs text-neutral-600">Other deployments to try.</p>
          </div>
          <div>
            <label className="label">Cooldown (s)</label>
            <input
              type="number" min={0} max={3600} className="input"
              value={form.cooldown_time}
              onChange={(e) => field("cooldown_time", +e.target.value)}
            />
            <p className="mt-1.5 text-xs text-neutral-600">Bench a key after it fails.</p>
          </div>
          <div>
            <label className="label">Allowed fails</label>
            <input
              type="number" min={1} max={100} className="input"
              value={form.allowed_fails}
              onChange={(e) => field("allowed_fails", +e.target.value)}
            />
            <p className="mt-1.5 text-xs text-neutral-600">Failures before benching.</p>
          </div>
        </div>

        {error && (
          <p className="rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-sm text-red-400">
            {error}
          </p>
        )}

        <div className="flex items-center gap-3">
          <button
            className="btn-pri"
            disabled={save.isPending}
            onClick={() => save.mutate(form)}
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
          <button className="btn-sec" onClick={() => reset.mutate()}>
            Reset to defaults
          </button>
          {saved && <span className="text-sm text-emerald-500">Saved</span>}
          {form.is_default && (
            <span className="ml-auto text-xs text-neutral-600">Using defaults</span>
          )}
        </div>
      </div>
    </div>
  );
}
