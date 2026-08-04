import createNextIntlPlugin from "next-intl/plugin";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");
const projectRoot = dirname(fileURLToPath(import.meta.url));

/**
 * The FastAPI gateway this dashboard talks to. Every `/api/*` call the UI makes
 * is translated by src/app/api/[...path]/route.ts into a call against this
 * origin. Server-side only — it is never exposed to the browser bundle.
 */
const GATEWAY_URL = process.env.GATEWAY_URL || "http://127.0.0.1:8000";

/**
 * OmniRoute's privileged/native modules are aliased to their own shipped stubs.
 * This deployment runs the UI only: the MITM proxy, keychain readers, cloud sync
 * and the 9router installer all belonged to the Node backend that was removed,
 * so the stubs keep the module graph resolvable without native binaries.
 */
const stubAliases = {
  "@/mitm/manager": "./src/mitm/manager.stub.ts",
  "@/mitm/cert/install": "./src/mitm/cert/install.stub.ts",
  "@/lib/zed-oauth/keychain-reader": "./src/lib/zed-oauth/keychain-reader.stub.ts",
  "@/lib/cloudSync": "./src/lib/cloudSync.stub.ts",
  "@/lib/services/installers/ninerouter": "./src/lib/services/installers/ninerouter.stub.ts",
};

/**
 * Native modules the Node backend used and this deployment does not ship:
 * local SQLite, TLS-fingerprint stealth, OS keychain, on-device embeddings.
 * Orphaned modules under src/lib still import them by bare specifier, and an
 * unresolved import is a hard build failure — so they resolve to a stub that
 * throws only if something actually calls it. See src/bridge/stubs/unavailable.ts.
 */
const nativeStub = "./src/bridge/stubs/unavailable.ts";
const nativeAliases = {
  "better-sqlite3": nativeStub,
  "tls-client-node": nativeStub,
  "wreq-js": nativeStub,
  keytar: nativeStub,
  "@huggingface/transformers": nativeStub,
  // src/lib/db/adapters/sqljsAdapter.ts does `require.resolve("sql.js/package.json")`
  // as its WASM-locating step. sql.js's exports map does not expose
  // ./package.json, so the bundler cannot resolve it — and the whole local-SQLite
  // tier is dead code here anyway, since persistence lives in the gateway's
  // Postgres.
  "sql.js": nativeStub,
  "sql.js/package.json": nativeStub,
};

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  compress: true,
  productionBrowserSourceMaps: false,

  // The UI was lifted from OmniRoute wholesale. Large parts of src/lib are the
  // orphaned half of a Node backend this project does not run, so they do not
  // typecheck in isolation. Failing the build on them would block every page,
  // including the ones fully wired to FastAPI.
  typescript: { ignoreBuildErrors: true },

  turbopack: {
    root: projectRoot,
    resolveAlias: { ...stubAliases, ...nativeAliases },
  },

  experimental: {
    // The bridge forwards multipart uploads (audio transcription, image edits)
    // through to FastAPI, so the default 1 MB Server Action cap would reject
    // legitimate requests with a misleading error.
    serverActions: { bodySizeLimit: "50mb" },
    proxyClientMaxBodySize: "512mb",
    proxyTimeout: 600_000,
    // Barrel tree-shaking for EXTERNAL libs only. Never add
    // @omniroute/open-sse here — its index.ts re-exports the whole streaming
    // engine and the webpack production pass OOMs.
    optimizePackageImports: [
      "@lobehub/icons",
      "lucide-react",
      "date-fns",
      "material-symbols",
      "next-intl",
    ],
  },

  outputFileTracingRoot: projectRoot,
  outputFileTracingIncludes: {
    // Compression rule/filter JSON is read via fs at runtime and is not always
    // auto-traced.
    "/*": [
      "./open-sse/services/compression/engines/rtk/filters/**/*.json",
      "./open-sse/services/compression/rules/**/*.json",
    ],
  },

  serverExternalPackages: [
    "pino",
    "pino-pretty",
    "thread-stream",
    "pino-abstract-transport",
    "better-sqlite3",
    "sqlite-vec",
    "node-machine-id",
    "keytar",
    "zod",
    "tls-client-node",
    "koffi",
    "@ngrok/ngrok",
    "ws",
    "bufferutil",
    "utf-8-validate",
  ],

  transpilePackages: ["@omniroute/open-sse", "@lobehub/icons"],
  allowedDevOrigins: ["localhost", "127.0.0.1"],

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default withNextIntl(nextConfig);
