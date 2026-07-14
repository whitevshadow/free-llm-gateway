/**
 * The "login" screen — except there is no login.
 *
 * The gateway key IS the credential (the backend has no passwords). So the entry
 * point is: paste your key, we validate it against GET /v1/me, and store it.
 *
 * We validate BEFORE storing. Storing an unverified key would leave the app in a
 * broken state where every page 401s and bounces back here — a loop with no
 * explanation. Better to fail once, here, with a clear message.
 */

import { useState } from "react";
import { api, setKey } from "../lib/api";
import type { Me } from "../lib/types";

export default function KeyGate({ onConnect }: { onConnect: (me: Me) => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await api.verifyKey(value.trim());
      setKey(value.trim());
      onConnect(me);
    } catch {
      setError("That key isn't valid, or it has been revoked.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={connect} className="w-full max-w-md">
        <div className="mb-8 text-center">
          <div className="mb-3 text-3xl">🛰️</div>
          <h1 className="text-xl font-semibold text-neutral-100">LLM Gateway</h1>
          <p className="mt-2 text-sm text-neutral-500">
            Paste your gateway key to continue.
          </p>
        </div>

        <div className="card">
          <label className="label" htmlFor="key">Gateway key</label>
          <input
            id="key"
            className="input font-mono"
            placeholder="sk-gw-…"
            value={value}
            autoFocus
            onChange={(e) => setValue(e.target.value)}
          />

          {error && (
            <p className="mt-3 rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-sm text-red-400">
              {error}
            </p>
          )}

          <button className="btn-pri mt-4 w-full" disabled={!value.trim() || busy}>
            {busy ? "Checking…" : "Connect"}
          </button>

          {/* There is no signup. Say so, rather than letting people hunt for one. */}
          <p className="mt-4 text-xs leading-relaxed text-neutral-600">
            No account? There's no signup — an admin creates your user and mints
            your key. On a fresh server, the first admin key is printed once in
            the startup logs.
          </p>
        </div>
      </form>
    </div>
  );
}
