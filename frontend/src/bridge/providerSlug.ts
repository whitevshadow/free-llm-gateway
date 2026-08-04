/**
 * OmniRoute catalog id → the gateway's provider slug.
 *
 * The two name the same providers differently, and the mismatch is not
 * cosmetic: it decides whether a request lands on a known preset or is treated
 * as an unknown provider that needs a base URL.
 *
 *   OmniRoute names the BRAND        nvidia, github-models, gemini
 *   The gateway names the SERVICE    nvidia_nim, github, gemini
 *                                    (backend/app/services/presets.py)
 *
 * Without this, clicking NVIDIA in the directory and pressing Check produced
 * "the gateway does not know 'nvidia'" — technically true and completely
 * unhelpful, since the gateway has known `nvidia_nim` all along.
 *
 * Applied server-side in the bridge rather than in a page, so EVERY provider
 * route agrees: validate, connect, discover and key-add all resolve the same id
 * the same way. A page-level fix would leave the others mismatched.
 *
 * Only exceptions are listed; anything absent passes through unchanged, which is
 * the correct behaviour for a custom provider registered under its own name.
 */

const SLUG_BY_CATALOG_ID: Record<string, string> = {
  // NVIDIA's OpenAI-compatible surface is NIM; the catalog card says "nvidia".
  nvidia: "nvidia_nim",
  "nvidia-nim": "nvidia_nim",
  nim: "nvidia_nim",

  // Google's key-based endpoint is AI Studio / Gemini.
  google: "gemini",
  "google-ai-studio": "gemini",
  googleaistudio: "gemini",
  "google-gemini": "gemini",

  // GitHub Models.
  "github-models": "github",
  githubmodels: "github",

  // HuggingFace Inference.
  hf: "huggingface",
  "hugging-face": "huggingface",
  "huggingface-inference": "huggingface",
};

/** Translate a catalog id to the slug the gateway knows it by. */
export function toGatewaySlug(id: string): string {
  const key = (id ?? "").trim().toLowerCase();
  return SLUG_BY_CATALOG_ID[key] ?? key;
}

/**
 * The reverse: gateway slug → the id the dashboard's catalog uses.
 *
 * Needed because the provider page routes on the CATALOG id (/dashboard/
 * providers/nvidia) and then filters connections by `provider === "nvidia"`.
 * Handing it `nvidia_nim` makes a provider with two live keys render
 * "0 connections" — the data is all there and simply never matches.
 *
 * Built by inverting the table above. Several catalog ids can map to one slug
 * (nvidia, nvidia-nim, nim → nvidia_nim), so the first mapping wins and the
 * aliases below it are ignored — which is why the canonical id is listed first.
 */
const CATALOG_ID_BY_SLUG: Record<string, string> = (() => {
  const out: Record<string, string> = {};
  for (const [catalogId, slug] of Object.entries(SLUG_BY_CATALOG_ID)) {
    if (!(slug in out)) out[slug] = catalogId;
  }
  return out;
})();

export function toCatalogId(slug: string): string {
  const key = (slug ?? "").trim().toLowerCase();
  return CATALOG_ID_BY_SLUG[key] ?? key;
}
