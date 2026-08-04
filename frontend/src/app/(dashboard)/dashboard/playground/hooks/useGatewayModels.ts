"use client";

/**
 * The Playground's model list, scoped to the selected gateway provider.
 *
 * Replaces the translator's `useAvailableModels` for this screen. That hook
 * filters a flat `/api/v1/models` list with `id.startsWith(provider + "/")`,
 * which assumes OmniRoute's convention of namespacing every model id by the
 * provider that serves it. This gateway does not do that: `/v1/models` returns
 * the callable id (`gpt-oss-120b`, `openai/gpt-oss-120b`, `command-r-08-2024`)
 * where any prefix is the model's PUBLISHER, not the provider. So selecting
 * "Cerebras" matched zero models, the `<select>` collapsed to a free-text box,
 * and the auto-default never fired — leaving whatever model was already in
 * state, typically one belonging to a different provider entirely.
 *
 * `/api/provider-models?provider=<catalogId>` is the gateway's own answer to
 * "what does this provider serve", and it carries per-model probe results, so
 * it also lets the default land on a model that actually works.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { compareTr } from "@/shared/utils/turkishText";
import type { ModelReasoningCapabilities } from "../components/reasoningControlUtils";

type ProviderModelEntry = {
  id?: string;
  isCallable?: boolean;
  capabilities?: ModelReasoningCapabilities;
};

export function useGatewayModels(provider?: string) {
  const [allModels, setAllModels] = useState<string[]>([]);
  const [callableModels, setCallableModels] = useState<string[]>([]);
  const [modelCapabilities, setModelCapabilities] = useState<
    Record<string, ModelReasoningCapabilities>
  >({});
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    try {
      // No provider selected ("Auto") means the whole routable catalog, which is
      // exactly what the OpenAI-compatible list endpoint returns.
      const url = provider
        ? `/api/provider-models?provider=${encodeURIComponent(provider)}`
        : "/api/v1/models";
      const res = await fetch(url, { signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      // Two shapes: `{ models: [...] }` from provider-models, `{ data: [...] }`
      // from the OpenAI-compatible list.
      const entries: ProviderModelEntry[] = Array.isArray(data?.models)
        ? data.models
        : Array.isArray(data?.data)
          ? data.data
          : [];

      const ids: string[] = [];
      const callable: string[] = [];
      const caps: Record<string, ModelReasoningCapabilities> = {};
      for (const entry of entries) {
        if (!entry || typeof entry.id !== "string" || !entry.id) continue;
        ids.push(entry.id);
        // `isCallable` only exists on the provider-models shape. When it is
        // absent every model is a candidate, which matches the old behaviour.
        if (entry.isCallable !== false) callable.push(entry.id);
        if (entry.capabilities) caps[entry.id] = entry.capabilities;
      }

      ids.sort(compareTr);
      callable.sort(compareTr);
      setAllModels(ids);
      setCallableModels(callable);
      setModelCapabilities(caps);
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      setAllModels([]);
      setCallableModels([]);
      setModelCapabilities({});
    } finally {
      setLoading(false);
    }
  }, [provider]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return useMemo(
    () => ({ availableModels: allModels, callableModels, modelCapabilities, loading }),
    [allModels, callableModels, modelCapabilities, loading]
  );
}
