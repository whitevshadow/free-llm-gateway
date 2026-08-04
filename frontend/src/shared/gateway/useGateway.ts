"use client";

/**
 * Reading from the gateway.
 *
 * Every SRS screen fetches through here so three behaviours are decided once:
 *
 *   1. A 501 is not an error. The bridge answers unimplemented paths with a
 *      documented envelope (src/bridge/notImplemented.ts), and a screen that
 *      showed that as a red failure would be lying — the gateway is fine, the
 *      feature is not built. It surfaces as `notImplemented` instead.
 *   2. Polling is opt-in per screen. Health and status screens want it; a model
 *      registry does not, and polling it would spend requests to redraw the same
 *      table.
 *   3. An in-flight request is abandoned when the component unmounts or the URL
 *      changes, so a slow response cannot overwrite a newer one.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export type GatewayState<T> = {
  data: T | null;
  loading: boolean;
  /** A real failure: unreachable gateway, 4xx/5xx that is not a 501. */
  error: string | null;
  /** The route exists in the UI but not in the gateway yet. */
  notImplemented: { path: string; plannedPhase: string | null; reason?: string } | null;
  refresh: () => void;
};

export function useGateway<T>(path: string, options: { pollMs?: number } = {}): GatewayState<T> {
  const { pollMs } = options;

  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notImplemented, setNotImplemented] =
    useState<GatewayState<T>["notImplemented"]>(null);

  // Bumped by refresh() to re-run the effect without making `path` a dependency
  // of a callback the caller would have to memoise.
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    async function load() {
      try {
        const response = await fetch(path, {
          signal: controller.signal,
          headers: { accept: "application/json" },
        });

        // Read the body regardless of status: the 501 envelope and the error
        // envelope both carry the detail worth showing.
        let body: Record<string, unknown> = {};
        try {
          body = (await response.json()) as Record<string, unknown>;
        } catch {
          body = {};
        }
        if (cancelled) return;

        if (response.status === 501 || body.notImplemented === true) {
          setNotImplemented({
            path: typeof body.path === "string" ? body.path : path,
            plannedPhase:
              typeof body.plannedPhase === "string" ? body.plannedPhase : null,
            reason: typeof body.reason === "string" ? body.reason : undefined,
          });
          setData(null);
          setError(null);
          return;
        }

        if (!response.ok) {
          setError(
            typeof body.error === "string"
              ? body.error
              : `Request failed (HTTP ${response.status}).`
          );
          setNotImplemented(null);
          return;
        }

        setData(body as T);
        setError(null);
        setNotImplemented(null);
      } catch (err) {
        // An abort is the expected outcome of navigating away, not a failure.
        if (cancelled || (err as Error).name === "AbortError") return;
        setError((err as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    setLoading(true);
    load();

    if (!pollMs) {
      return () => {
        cancelled = true;
        controller.abort();
      };
    }

    const timer = setInterval(load, pollMs);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(timer);
    };
  }, [path, pollMs, nonce]);

  return { data, loading, error, notImplemented, refresh };
}
