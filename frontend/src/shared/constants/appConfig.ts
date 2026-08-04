import pkg from "../../../package.json" with { type: "json" };

/**
 * The product this dashboard belongs to.
 *
 * The UI was ported from OmniRoute; the product is the Free LLM Gateway
 * described in SRS.md. This constant is the single place the name is defined —
 * the sidebar, the login screen and the document title all read it, so a
 * rebrand is one edit rather than a search across 2,000 files.
 */
export const APP_CONFIG = {
  name: "Free LLM Gateway",
  description: "OpenAI-compatible gateway across multiple free providers and keys",
  version: pkg.version,
};

export const THEME_CONFIG = {
  storageKey: "theme",
  defaultTheme: "system",
};
