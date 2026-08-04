/**
 * Stand-in for native modules this deployment does not ship.
 *
 * better-sqlite3, tls-client-node, wreq-js, keytar and @huggingface/transformers
 * were used by OmniRoute's Node backend for local storage, TLS fingerprint
 * stealth, OS keychain access and on-device embeddings. None of that runs here —
 * the backend is FastAPI — but orphaned modules in src/lib still import them,
 * and an unresolved bare import fails the build outright.
 *
 * Aliased in next.config.mjs. Anything that actually calls into one of these at
 * runtime throws a named error rather than failing obscurely, so a code path
 * that genuinely needs a native module is easy to spot.
 */

const MESSAGE =
  "This module is not available: the native dependency was removed when the " +
  "OmniRoute Node backend was replaced by the FastAPI gateway.";

function unavailable(): never {
  throw new Error(MESSAGE);
}

const handler: ProxyHandler<Record<string, unknown>> = {
  get: (_target, prop) => {
    if (prop === "then") return undefined; // never look like a thenable
    if (prop === Symbol.toStringTag) return "UnavailableModule";
    return unavailable;
  },
  apply: unavailable,
  construct: unavailable,
};

const stub = new Proxy({} as Record<string, unknown>, handler);

export default stub;
export const Database = stub;
export const pipeline = unavailable;
export const env = {};
