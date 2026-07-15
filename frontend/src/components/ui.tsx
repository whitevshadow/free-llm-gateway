/** Small shared pieces used across pages. */

import { useEffect, useRef, useState } from "react";

import type { MyModel } from "../lib/types";

/**
 * Multi-select filter dropdown. Empty selection means "no constraint" — the
 * button then reads "Label: All". Options carry an optional count so the user
 * can see what a tick will give them BEFORE clicking (mirrors the counts the
 * old publisher sidebar showed).
 */
export function FilterDropdown({
  label, options, selected, onChange,
}: {
  label: string;
  options: { value: string; count?: number }[];
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  function toggle(value: string) {
    const next = new Set(selected);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    onChange(next);
  }

  const active = selected.size > 0;

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm transition-colors ${
          active
            ? "border-emerald-700 bg-emerald-950/20 text-emerald-300"
            : "border-neutral-800 bg-neutral-900 text-neutral-400 hover:border-neutral-700"
        }`}
      >
        {label}:{" "}
        <span className={active ? "font-medium" : ""}>
          {active ? (selected.size === 1 ? [...selected][0] : selected.size) : "All"}
        </span>
        <span className="text-xs opacity-60">▾</span>
      </button>

      {open && (
        <div className="absolute left-0 top-full z-20 mt-1 w-64 rounded-xl border border-neutral-800 bg-neutral-900 p-1 shadow-xl">
          {active && (
            <button
              type="button"
              className="mb-1 w-full rounded-lg px-2 py-1.5 text-left text-xs text-neutral-500 hover:bg-neutral-800/60 hover:text-neutral-300"
              onClick={() => onChange(new Set())}
            >
              Clear ({label}: All)
            </button>
          )}
          <ul className="max-h-72 overflow-y-auto">
            {options.map(({ value, count }) => {
              const checked = selected.has(value);
              return (
                <li key={value}>
                  <label
                    className={`flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm transition-colors hover:bg-neutral-800/60 ${
                      checked ? "text-neutral-100" : "text-neutral-400"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(value)}
                      className="h-4 w-4 rounded accent-emerald-600"
                    />
                    <span className="flex-1 truncate">{value}</span>
                    {count !== undefined && (
                      <span className="tabular-nums text-xs text-neutral-600">{count}</span>
                    )}
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

export function StatCard({
  label, value, sub, tone = "default",
}: {
  label: string;
  value: string | number;
  sub?: string;
  tone?: "default" | "warn";
}) {
  return (
    <div className="card">
      <div className="label mb-2">{label}</div>
      <div
        className={`text-2xl font-semibold tabular-nums ${
          tone === "warn" ? "text-amber-400" : "text-neutral-100"
        }`}
      >
        {typeof value === "number" ? value.toLocaleString() : value}
      </div>
      {sub && <div className="mt-1 text-xs text-neutral-600">{sub}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-neutral-500">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-neutral-700 border-t-emerald-500" />
      {label ?? "Loading…"}
    </div>
  );
}

export function Empty({ title, body, action }: {
  title: string; body: string; action?: React.ReactNode;
}) {
  return (
    <div className="card flex flex-col items-center py-14 text-center">
      <h3 className="text-base font-medium text-neutral-200">{title}</h3>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-neutral-500">{body}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/**
 * The redundancy badge — the most information-dense thing in the UI.
 *
 * These are THREE DIFFERENT QUESTIONS and conflating them would mislead:
 *
 *   has_backup_provider  2+ live providers. Survives a whole provider going down.
 *   has_backup_key       2+ live deployments — INCLUDING two keys at the same
 *                        provider. Two Groq keys is real redundancy: when one is
 *                        rate-limited the other serves. It does NOT survive Groq
 *                        itself failing.
 *   neither              single point of failure. THIS is the row that should
 *                        make the user go add a second key.
 *
 * Deliberately absent: is_common. It only means "2+ providers serve this model
 * somewhere in the world" — it can be true while this user has no backup at all,
 * so showing it as a reassurance would be a lie.
 */
export function RedundancyBadge({ model }: { model: MyModel }) {
  if (model.has_backup_provider) {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-md bg-emerald-950/50 px-2 py-1 text-xs text-emerald-400"
        title="Two or more providers are live for this model. It survives a whole provider outage."
      >
        🛡 Provider backup
      </span>
    );
  }
  if (model.has_backup_key) {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-md bg-sky-950/50 px-2 py-1 text-xs text-sky-400"
        title="Two or more of your keys are live. If one is rate-limited, another serves — but all are on the same provider."
      >
        🔑 Key backup
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md bg-amber-950/50 px-2 py-1 text-xs text-amber-400"
      title="Only one live deployment. If it fails or hits its rate limit, this model is unavailable."
    >
      ⚠ No backup
    </span>
  );
}

/**
 * Classify a model's status into a REASON, not just up/down.
 *
 * A model row aggregates several deployments, so `statuses` can hold a mix
 * (one key auth-dead, another rate-limited). We show the most ACTIONABLE
 * reason: an auth failure needs the user to fix a key (nothing else will
 * revive it), a 429 just needs patience, a 404 means the provider doesn't
 * serve it to this account, and timeout/error mean "exists but inference
 * failed". "Deprecated" is deliberately absent: when a provider removes a
 * model, discovery drops it from the catalog entirely, so there is no row
 * left to badge.
 */
export interface StatusInfo {
  label: string;
  dot: string;    // tailwind bg- class for the dot
  text: string;   // tailwind text- class for the label
  hint: string;   // tooltip
}

export function statusInfo(m: Pick<MyModel, "is_usable" | "statuses">): StatusInfo {
  if (m.is_usable) {
    return {
      label: "Live", dot: "bg-emerald-500", text: "text-neutral-300",
      hint: "Working normally — at least one of your keys answers.",
    };
  }
  const s = new Set(m.statuses ?? []);
  if (s.has("auth_error")) {
    return {
      label: "Auth failed", dot: "bg-sky-500", text: "text-sky-400",
      hint: "Invalid or expired API key. No amount of waiting fixes this — replace the key under Providers & keys.",
    };
  }
  if (s.has("rate_limited")) {
    return {
      label: "Rate limited", dot: "bg-orange-500", text: "text-orange-400",
      hint: "The provider returned 429. Your key works — it's just throttled and will come back after its cooldown.",
    };
  }
  if (s.has("unavailable")) {
    return {
      label: "No access", dot: "bg-red-500", text: "text-red-400",
      hint: "The provider lists this model but doesn't serve it to your account (404).",
    };
  }
  if (s.has("timeout") || s.has("error")) {
    return {
      label: "Check failed", dot: "bg-yellow-500", text: "text-yellow-400",
      hint: "The model exists but the test request failed (timeout or provider error). Often transient — re-test.",
    };
  }
  return {
    label: "Unavailable", dot: "bg-neutral-600", text: "text-neutral-600",
    hint: "Not yet probed.",
  };
}

export function StatusDot({ ok, cooling }: { ok: boolean; cooling?: boolean }) {
  const cls = cooling
    ? "bg-amber-500"
    : ok
    ? "bg-emerald-500"
    : "bg-neutral-600";
  return <span className={`inline-block h-2 w-2 rounded-full ${cls}`} />;
}
