"use client";

import { useTranslations } from "next-intl";

/**
 * 403 Forbidden Page — Phase 8.1
 *
 * Displayed when access is denied due to:
 * - Invalid API key
 * - IP not in allowlist
 * - Rate limit exceeded
 */

import Link from "next/link";

export default function ForbiddenPage() {
  const t = useTranslations("auth");
  return (
    <div className="flex flex-col items-center justify-center min-h-screen p-6 bg-[var(--bg-primary,#0a0a0f)] text-[var(--text-primary,#e0e0e0)] text-center">
      <div
        className="text-[96px] font-extrabold leading-none mb-2"
        style={{
          background: "linear-gradient(135deg, #ef4444 0%, #f97316 50%, #eab308 100%)",
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
        }}
      >
        403
      </div>
      <h1 className="text-2xl font-semibold mb-2">{t("accessDenied")}</h1>
      <p className="text-[15px] text-[var(--text-secondary,#888)] max-w-[400px] leading-relaxed mb-8">
        {t("accessDeniedDescription")}
      </p>
      <Link
        href="/dashboard"
        className="bg-brand-gradient shadow-warm px-8 py-3 rounded-control text-white text-sm font-semibold no-underline transition-all duration-200 hover:-translate-y-0.5"
      >
        {t("goToDashboard")}
      </Link>
    </div>
  );
}
