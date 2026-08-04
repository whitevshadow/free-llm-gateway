/**
 * Gateway provider slug → the id ProviderIcon knows.
 *
 * The gateway's slugs come from backend/app/services/presets.py and name the
 * SERVICE (`nvidia_nim`, `github`), while the icon set is keyed by BRAND
 * (`nvidia`, `github`). Where the two already agree the slug passes straight
 * through, so this table only carries the exceptions.
 *
 * An unmapped slug is not a bug: ProviderIcon falls back to a generic mark, so a
 * newly seeded provider renders sensibly before anyone touches this file.
 */

const ICON_ID_BY_SLUG: Record<string, string> = {
  nvidia_nim: "nvidia",
  nvidia_nvcf: "nvidia",
  google_ai_studio: "google",
  gemini: "google",
  github_models: "github",
  openai_compatible: "openai",
  azure_openai: "azure",
  vertex_ai: "vertexai",
};

export function providerIconId(slug: string): string {
  return ICON_ID_BY_SLUG[slug] ?? slug;
}
