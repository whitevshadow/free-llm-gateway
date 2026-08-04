/**
 * The provider catalog, and what this gateway can actually do with each entry.
 *
 * OmniRoute ships a ~286-provider display catalog in
 * src/shared/constants/providers/. Each card carries an id, name, icon, colour,
 * website and a text hint — and deliberately NOT a base URL or an auth spec,
 * because OmniRoute's own Node engine (open-sse/, ~1,240 files) held the
 * endpoint and per-provider request transformer for each one.
 *
 * This project does not run that engine. It routes through LiteLLM against a
 * base URL plus a bearer key. So the cards are still worth showing — they are a
 * good directory of who exists and where to get a key — but each one has to be
 * classified by whether it can be wired up here at all:
 *
 *   preset     the backend knows it by name (presets.py): paste a key, done.
 *   custom     an API-key provider the backend does not know. Workable, but the
 *              base URL has to be supplied — usually named in the card's hint.
 *   unroutable OAuth, IDE, web-cookie, local and cloud-agent providers. These
 *              need a sign-in flow, a browser cookie or a local process that
 *              this gateway has no way to perform. Marked, never silently
 *              offered.
 *
 * The classification is the honest part of this screen. Rendering 286 identical
 * cards where 276 fail on submit would look richer and be worse.
 */

import {
  APIKEY_PROVIDERS,
  OAUTH_PROVIDERS,
  NOAUTH_PROVIDERS,
  WEB_COOKIE_PROVIDERS,
  LOCAL_PROVIDERS,
  CLOUD_AGENT_PROVIDERS,
} from "@/shared/constants/providers";

export type Routability = "preset" | "custom" | "unroutable";

export type CatalogEntry = {
  id: string;
  name: string;
  icon?: string;
  color?: string;
  textIcon?: string;
  website?: string;
  apiHint?: string;
  hasFree?: boolean;
  freeNote?: string;
  /** Which catalog section it came from — drives the filter chips. */
  category: "apikey" | "oauth" | "noauth" | "web-cookie" | "local" | "cloud-agent";
  routability: Routability;
  /** Why it cannot be wired up, when routability is "unroutable". */
  blockedReason?: string;
};

const BLOCKED_REASON: Record<string, string> = {
  oauth:
    "Signs in through an OAuth flow and rotates tokens. This gateway authenticates with a bearer key it holds, so there is nothing to paste.",
  "web-cookie":
    "Authenticates with a browser session cookie captured from a logged-in page. The gateway has no browser.",
  local:
    "Runs as a local process on the machine hosting the provider. The gateway calls remote HTTP endpoints only.",
  "cloud-agent":
    "A hosted agent runtime rather than a chat-completions endpoint. Nothing for the router to call.",
  noauth:
    "Keyless provider wired into OmniRoute's own engine. Reachable here only if it exposes an OpenAI-compatible URL — add it as a custom provider.",
};

/**
 * Slugs the backend has a preset for (backend/app/services/presets.py). Kept as
 * a literal list rather than fetched, so the grid can classify cards on first
 * paint; the configure screen re-checks against the live preset list before it
 * does anything, so a drift here costs a label, never a wrong action.
 */
export const BACKEND_PRESET_SLUGS = new Set([
  "groq",
  "cerebras",
  "nvidia_nim",
  "openrouter",
  "gemini",
  "mistral",
  "cohere",
  "github",
  "huggingface",
  "longcat",
]);

/** Catalog ids that mean the same provider as a backend preset under another name. */
const ID_TO_PRESET_SLUG: Record<string, string> = {
  google: "gemini",
  "google-ai-studio": "gemini",
  googleaistudio: "gemini",
  nvidia: "nvidia_nim",
  "nvidia-nim": "nvidia_nim",
  "github-models": "github",
  githubmodels: "github",
  hf: "huggingface",
  "hugging-face": "huggingface",
};

export function presetSlugFor(catalogId: string): string | null {
  const direct = ID_TO_PRESET_SLUG[catalogId] ?? catalogId;
  return BACKEND_PRESET_SLUGS.has(direct) ? direct : null;
}

function classify(
  id: string,
  category: CatalogEntry["category"]
): { routability: Routability; blockedReason?: string } {
  if (presetSlugFor(id)) return { routability: "preset" };
  if (category === "apikey") return { routability: "custom" };
  return { routability: "unroutable", blockedReason: BLOCKED_REASON[category] };
}

function section(
  source: Record<string, unknown>,
  category: CatalogEntry["category"]
): CatalogEntry[] {
  return Object.values(source ?? {}).map((raw) => {
    const p = raw as Record<string, unknown>;
    const id = String(p.id ?? "");
    return {
      id,
      name: String(p.name ?? id),
      icon: p.icon as string | undefined,
      color: p.color as string | undefined,
      textIcon: p.textIcon as string | undefined,
      website: p.website as string | undefined,
      apiHint: p.apiHint as string | undefined,
      hasFree: Boolean(p.hasFree),
      freeNote: p.freeNote as string | undefined,
      category,
      ...classify(id, category),
    };
  });
}

let cached: CatalogEntry[] | null = null;

/** The whole catalog, classified. Built once — it is static data. */
export function getCatalog(): CatalogEntry[] {
  if (cached) return cached;
  cached = [
    ...section(APIKEY_PROVIDERS as Record<string, unknown>, "apikey"),
    ...section(OAUTH_PROVIDERS as Record<string, unknown>, "oauth"),
    ...section(NOAUTH_PROVIDERS as Record<string, unknown>, "noauth"),
    ...section(WEB_COOKIE_PROVIDERS as Record<string, unknown>, "web-cookie"),
    ...section(LOCAL_PROVIDERS as Record<string, unknown>, "local"),
    ...section(CLOUD_AGENT_PROVIDERS as Record<string, unknown>, "cloud-agent"),
  ]
    .filter((e) => e.id)
    .sort((a, b) => a.name.localeCompare(b.name));
  return cached;
}

export function getCatalogEntry(id: string): CatalogEntry | undefined {
  return getCatalog().find((e) => e.id === id);
}

/**
 * Pull a likely base URL out of a card's hint text.
 *
 * The hints are prose written for humans ("Create an API key at
 * https://hyper.charm.land, then paste it here"), so this is a convenience that
 * pre-fills the field — never a substitute for the user confirming it. Only
 * api-ish hosts are offered, because a hint's first URL is usually the signup
 * page, not the endpoint.
 */
export function guessBaseUrl(entry: CatalogEntry | undefined): string {
  if (!entry) return "";
  const text = `${entry.apiHint ?? ""} ${entry.website ?? ""}`;
  const urls = text.match(/https?:\/\/[^\s,)"']+/g) ?? [];
  const apiish = urls.find((u) => /\/v\d|api\./i.test(u));
  if (!apiish) return "";
  return apiish.replace(/[.,);]+$/, "");
}
