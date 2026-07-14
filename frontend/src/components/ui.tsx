/** Small shared pieces used across pages. */

import type { MyModel } from "../lib/types";

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

export function StatusDot({ ok, cooling }: { ok: boolean; cooling?: boolean }) {
  const cls = cooling
    ? "bg-amber-500"
    : ok
    ? "bg-emerald-500"
    : "bg-neutral-600";
  return <span className={`inline-block h-2 w-2 rounded-full ${cls}`} />;
}
