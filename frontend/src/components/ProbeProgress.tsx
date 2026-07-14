/**
 * The probe progress bar — shown while Discover / Re-test / a new key's fan-out
 * is being tested in the background.
 *
 * Probing is deliberately slow (a handful of requests at a time, so we don't
 * rate-limit the very keys we're validating), which means NVIDIA's ~106 models
 * across two keys is a couple of minutes. A spinner that just… spins for two
 * minutes reads as broken; "137 of 212" reads as working.
 *
 * Polls fast (1s) only while a job is running, slow (5s) otherwise — the
 * endpoint is an in-memory lookup, so this is cheap. When the job FINISHES, it
 * invalidates the models/keys queries once, so the counts on the page snap to
 * their final values without the user refreshing.
 */

import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../lib/api";

export default function ProbeProgress() {
  const qc = useQueryClient();

  const { data } = useQuery({
    queryKey: ["probe-status"],
    queryFn: api.probeStatus,
    refetchInterval: (q) =>
      (q.state.data as { active?: boolean } | undefined)?.active ? 1000 : 5000,
  });

  // On the active → idle transition, refresh everything the probe changed.
  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && data && !data.active) {
      qc.invalidateQueries({ queryKey: ["my-models"] });
      qc.invalidateQueries({ queryKey: ["my-keys"] });
      qc.invalidateQueries({ queryKey: ["providers"] });
    }
    wasActive.current = !!data?.active;
  }, [data, qc]);

  if (!data?.active) return null;

  const pct = data.total > 0 ? Math.round((data.done / data.total) * 100) : 0;

  return (
    <div className="rounded-xl border border-emerald-900/50 bg-emerald-950/20 px-4 py-3">
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="text-emerald-300">
          Testing models… a few at a time, so we don't rate-limit your keys.
        </span>
        <span className="tabular-nums text-emerald-400">
          {data.done} / {data.total}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-neutral-800">
        <div
          className="h-full rounded-full bg-emerald-500 transition-all duration-700"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
