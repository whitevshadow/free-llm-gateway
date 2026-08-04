"use client";

/**
 * Shared presentation for the SRS screens.
 *
 * Styling goes through the theme's CSS variables (--color-surface, --color-border,
 * …) rather than fixed Tailwind palette classes, so these screens follow the
 * light/dark toggle the ported design system already ships.
 *
 * `StatusPill` is the important one: SRS §13.2 defines six health statuses whose
 * differences are the whole point of the gateway — a 429 means the key works and
 * is throttled, a 401 means it is dead and no timer may revive it. Rendering
 * both as a generic red "error" would erase that. One component owns the mapping
 * so every screen says the same thing about the same status.
 */

import type { ReactNode } from "react";

// ── Health status ───────────────────────────────────────────────────────────

const STATUS_STYLE: Record<string, { bg: string; fg: string; label: string; hint: string }> = {
  available: {
    bg: "rgba(34,197,94,0.12)",
    fg: "#22c55e",
    label: "available",
    hint: "Serving requests.",
  },
  rate_limited: {
    bg: "rgba(245,158,11,0.14)",
    fg: "#f59e0b",
    label: "rate limited",
    hint: "The key works and is throttled. Revives on its own when the cooldown expires.",
  },
  auth_error: {
    bg: "rgba(239,68,68,0.14)",
    fg: "#ef4444",
    label: "auth error",
    hint: "The key is dead. No timer will resurrect it — replace it.",
  },
  timeout: {
    bg: "rgba(168,85,247,0.14)",
    fg: "#a855f7",
    label: "timeout",
    hint: "No response within 20s.",
  },
  unavailable: {
    bg: "rgba(113,113,122,0.16)",
    fg: "#a1a1aa",
    label: "unavailable",
    hint: "Listed in the catalog but not actually served (404).",
  },
  error: {
    bg: "rgba(239,68,68,0.10)",
    fg: "#f87171",
    label: "error",
    hint: "Something else went wrong.",
  },
};

export function StatusPill({ status, title }: { status: string; title?: string }) {
  const style = STATUS_STYLE[status] ?? STATUS_STYLE.error;
  return (
    <span
      title={title ?? style.hint}
      className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap"
      style={{ background: style.bg, color: style.fg }}
    >
      {style.label}
    </span>
  );
}

export function statusHint(status: string): string {
  return (STATUS_STYLE[status] ?? STATUS_STYLE.error).hint;
}

// ── Layout ──────────────────────────────────────────────────────────────────

export function PageHeader({
  title,
  srs,
  description,
  actions,
}: {
  title: string;
  /** The SRS section this screen implements. Shown so a reader can trace any
   *  screen back to the requirement that asked for it. */
  srs?: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-semibold" style={{ color: "var(--color-text-main)" }}>
            {title}
          </h1>
          {srs && (
            <span
              className="rounded px-1.5 py-0.5 text-[11px] font-mono"
              style={{ background: "var(--color-bg-subtle)", color: "var(--color-text-muted)" }}
              title="The SRS section this screen implements"
            >
              SRS {srs}
            </span>
          )}
        </div>
        {description && (
          <p className="mt-1 text-sm" style={{ color: "var(--color-text-muted)" }}>
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Panel({
  title,
  subtitle,
  actions,
  children,
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      className="rounded-xl border"
      style={{ background: "var(--color-surface)", borderColor: "var(--color-border)" }}
    >
      {(title || actions) && (
        <header
          className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3"
          style={{ borderColor: "var(--color-border)" }}
        >
          <div>
            {title && (
              <h2 className="text-sm font-semibold" style={{ color: "var(--color-text-main)" }}>
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
                {subtitle}
              </p>
            )}
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "default" | "good" | "warn" | "bad";
}) {
  const toneColor =
    tone === "good" ? "#22c55e" : tone === "warn" ? "#f59e0b" : tone === "bad" ? "#ef4444" : undefined;
  return (
    <div
      className="rounded-xl border px-4 py-3"
      style={{ background: "var(--color-surface)", borderColor: "var(--color-border)" }}
    >
      <div className="text-xs uppercase tracking-wide" style={{ color: "var(--color-text-muted)" }}>
        {label}
      </div>
      <div
        className="mt-1 text-2xl font-semibold tabular-nums"
        style={{ color: toneColor ?? "var(--color-text-main)" }}
      >
        {value}
      </div>
      {hint && (
        <div className="mt-0.5 text-xs" style={{ color: "var(--color-text-muted)" }}>
          {hint}
        </div>
      )}
    </div>
  );
}

/** Wide tables scroll inside their own container; the page body never does. */
export function TableScroll({ children }: { children: ReactNode }) {
  return <div className="overflow-x-auto">{children}</div>;
}

export function Th({ children, right }: { children: ReactNode; right?: boolean }) {
  return (
    <th
      className={`whitespace-nowrap px-3 py-2 text-xs font-medium ${right ? "text-right" : "text-left"}`}
      style={{ color: "var(--color-text-muted)" }}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  right,
  mono,
}: {
  children: ReactNode;
  right?: boolean;
  mono?: boolean;
}) {
  return (
    <td
      className={`whitespace-nowrap px-3 py-2 text-sm ${right ? "text-right tabular-nums" : ""} ${mono ? "font-mono text-xs" : ""}`}
      style={{ color: "var(--color-text-main)" }}
    >
      {children}
    </td>
  );
}

// ── States ──────────────────────────────────────────────────────────────────

export function LoadingRow({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="px-4 py-10 text-center text-sm" style={{ color: "var(--color-text-muted)" }}>
      {label}
    </div>
  );
}

export function ErrorRow({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="px-4 py-8 text-center">
      <p className="text-sm" style={{ color: "#ef4444" }}>
        {message}
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-3 rounded-lg border px-3 py-1.5 text-xs"
          style={{ borderColor: "var(--color-border)", color: "var(--color-text-main)" }}
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyRow({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="px-4 py-12 text-center">
      <p className="text-sm" style={{ color: "var(--color-text-main)" }}>
        {title}
      </p>
      {hint && (
        <p className="mx-auto mt-1 max-w-md text-xs" style={{ color: "var(--color-text-muted)" }}>
          {hint}
        </p>
      )}
    </div>
  );
}

/**
 * Shown when the gateway does not implement a path yet. Distinct from an error
 * on purpose: nothing is broken, the feature simply is not built.
 */
export function NotWired({
  path,
  plannedPhase,
  reason,
}: {
  path: string;
  plannedPhase: string | null;
  reason?: string;
}) {
  return (
    <div className="px-4 py-12 text-center">
      <p className="text-sm font-medium" style={{ color: "var(--color-text-main)" }}>
        Not wired to the gateway yet
      </p>
      <p className="mx-auto mt-2 max-w-lg text-xs" style={{ color: "var(--color-text-muted)" }}>
        {reason ?? (
          <>
            The dashboard calls <code className="font-mono">{path}</code>, which this gateway does
            not implement.
          </>
        )}
      </p>
      {plannedPhase && (
        <p className="mt-2 text-xs" style={{ color: "var(--color-text-muted)" }}>
          Planned: <strong>{plannedPhase}</strong> — see OMNIROUTE_INTEGRATION.md
        </p>
      )}
    </div>
  );
}

// ── Formatting ──────────────────────────────────────────────────────────────

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 0) return "in the future";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function duration(seconds: number): string {
  if (seconds <= 0) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

export function compact(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}
