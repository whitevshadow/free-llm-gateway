/** Mirrors the backend responses. Kept hand-written and small on purpose. */

export interface Me {
  id: number;
  public_id: string;
  email: string | null;
  is_admin: boolean;
  /** 0 => show onboarding, not an empty dashboard. */
  provider_key_count: number;
}

export interface ProviderInfo {
  id: number;
  slug: string;
  name: string;
  base_url: string | null;
  docs_url: string | null;
  /** What kind of key this provider wants (from the preset), if known. */
  key_hint?: string | null;
  model_count: number;
  /** Present on /v1/providers (user view). */
  has_my_key?: boolean;
  /** Present on /v1/admin/providers (admin view). */
  enabled?: boolean;
}

export interface MyProviderKey {
  id: number;
  provider: string;
  label: string;
  masked: string;
  is_active: boolean;
  working_models: number;
  total_models: number;
}

/**
 * One model family, as THIS user can reach it.
 *
 * The three flags are three different questions — see RedundancyBadge:
 *   is_common            catalog badge only. NOT a routing signal, NOT a fallback.
 *   has_backup_key       2+ live deployments. Two keys at the SAME provider counts.
 *   has_backup_provider  2+ live providers. Survives a whole provider outage.
 */
export interface MyModel {
  model: string;
  /** 'chat' | 'embedding' | 'image' | 'audio' — each is its own /v1 surface. */
  mode: string;
  /** Normalized publishing org ('Meta', 'Mistral AI') — powers the right-side filter. */
  publisher: string | null;
  providers: string[];
  is_common: boolean;
  is_usable: boolean;
  has_backup_key: boolean;
  has_backup_provider: boolean;
  live_keys: number;
  total_keys: number;
  /**
   * Distinct probe outcomes across this model's deployments:
   * 'available' | 'rate_limited' | 'auth_error' | 'unavailable' | 'timeout' | 'error'.
   * When is_usable is false, these say WHY (see statusInfo in ui.tsx).
   */
  statuses: string[];
  last_checked_at: string | null;
}

export interface Usage {
  window_days: number;
  totals: {
    requests: number;
    total_tokens: number;
    prompt_tokens: number;
    completion_tokens: number;
    cost: number;
    avg_latency_ms: number;
    errors: number;
  };
  top_models: { model: string; tokens: number; requests: number }[];
  top_providers: { provider: string; slug: string; tokens: number; requests: number }[];
  daily: { date: string; tokens: number; requests: number }[];
  per_key: {
    id: number;
    label: string;
    masked: string;
    provider: string;
    requests: number;
    tokens: number;
    working_models: number;
  }[];
}

export interface LogRow {
  id: number;
  created_at: string | null;
  model: string;
  provider: string | null;
  key_label: string | null;
  key_masked: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  latency_ms: number | null;
  status_code: number;
  error: string | null;
}

export interface RouterConfig {
  routing_strategy: string;
  num_retries: number;
  cooldown_time: number;
  allowed_fails: number;
  is_default: boolean;
}

export interface AdminUser {
  id: number;
  email: string | null;
  is_admin: boolean;
  is_active: boolean;
}

export interface MintedKey {
  id: number;
  user_id: number;
  name: string;
  /** Returned ONCE. Unrecoverable. The UI must make the user copy it. */
  token: string;
  key_prefix: string;
}

/** A known provider offered in the "Add a provider" dropdown. */
export interface Preset {
  slug: string;
  name: string;
  base_url: string;
  docs_url: string | null;
  hint: string;
}

/**
 * Result of registering a provider. Registration is key-less by design: the
 * catalog fills in when the first API key for it is added under Providers &
 * keys (that key is what discovery calls /v1/models with).
 */
export interface AddProviderResult {
  id: number;
  slug: string;
  name: string;
  base_url: string | null;
  is_preset: boolean;
  status: "created";
  detail: string;
}
