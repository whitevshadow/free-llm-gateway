/**
 * Admin — seed providers, create users, mint their gateway keys.
 *
 * This page is only REACHABLE by an admin, but that is not what makes it safe:
 * the backend's require_admin returns 403 regardless. Hiding the nav item is
 * cosmetic. If you ever find yourself relying on this component for security,
 * something has gone wrong.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "../lib/api";
import type { AddProviderResult, MintedKey, ProviderInfo } from "../lib/types";
import { Spinner } from "../components/ui";

export default function Admin() {
  const qc = useQueryClient();
  const [minted, setMinted] = useState<MintedKey | null>(null);
  const [addingProvider, setAddingProvider] = useState(false);
  const [creatingUser, setCreatingUser] = useState(false);
  const [deleting, setDeleting] = useState<ProviderInfo | null>(null);

  const providers = useQuery({ queryKey: ["admin-providers"], queryFn: api.admin.providers });
  const users = useQuery({ queryKey: ["admin-users"], queryFn: api.admin.users });

  const discover = useMutation({
    mutationFn: (slug: string) => api.admin.discover(slug),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-providers"] }),
  });

  const mint = useMutation({
    mutationFn: (userId: number) => api.admin.mintKey(userId),
    onSuccess: (key) => setMinted(key),
  });

  const remove = useMutation({
    mutationFn: (slug: string) => api.admin.deleteProvider(slug),
    onSuccess: () => {
      setDeleting(null);
      qc.invalidateQueries({ queryKey: ["admin-providers"] });
    },
  });

  const toggle = useMutation({
    mutationFn: ({ slug, enabled }: { slug: string; enabled: boolean }) =>
      api.admin.toggleProvider(slug, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-providers"] }),
  });

  if (providers.isLoading || users.isLoading) return <Spinner />;

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold text-neutral-100">Admin</h1>

      {/* ── providers ── */}
      <div className="card">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="font-medium text-neutral-200">Providers</h2>
            <p className="text-xs text-neutral-600">
              The global catalog. Users attach their own keys to these.
            </p>
          </div>
          <button className="btn-pri" onClick={() => setAddingProvider(true)}>
            + Add provider
          </button>
        </div>

        <table className="w-full">
          <thead>
            <tr>
              <th className="th">Slug</th>
              <th className="th">Name</th>
              <th className="th">Base URL</th>
              <th className="th text-right">Models</th>
              <th className="th">Enabled</th>
              <th className="th"></th>
            </tr>
          </thead>
          <tbody>
            {providers.data!.providers.map((p) => (
              <tr key={p.id} className={p.enabled === false ? "opacity-50" : ""}>
                <td className="td font-mono text-neutral-300">{p.slug}</td>
                <td className="td text-neutral-300">{p.name}</td>
                <td className="td text-xs text-neutral-600">{p.base_url ?? "(default)"}</td>
                <td className="td text-right tabular-nums">{p.model_count ?? 0}</td>
                <td className="td">
                  {/* Disable = reversible pause: nothing is deleted, but the
                      provider leaves every user's routing set (enforced in the
                      DB views) and users can't attach new keys to it. */}
                  <button
                    role="switch"
                    aria-checked={p.enabled !== false}
                    onClick={() =>
                      toggle.mutate({ slug: p.slug, enabled: p.enabled === false })
                    }
                    disabled={toggle.isPending}
                    title={
                      p.enabled === false
                        ? "Disabled: hidden from users and excluded from routing. Click to re-enable."
                        : "Enabled. Click to disable — keys and models are kept, but nothing routes to it."
                    }
                    className={`relative h-5 w-9 rounded-full transition-colors ${
                      p.enabled !== false ? "bg-emerald-600" : "bg-neutral-700"
                    }`}
                  >
                    <span
                      className="absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all"
                      style={{ left: p.enabled !== false ? "1.125rem" : "0.125rem" }}
                    />
                  </button>
                </td>
                <td className="td text-right">
                  <div className="flex justify-end gap-2">
                    <button
                      className="btn-sec text-xs"
                      onClick={() => discover.mutate(p.slug)}
                      disabled={discover.isPending}
                      // Discovery borrows an existing key to call the provider's
                      // /v1/models. With no key anywhere, it cannot work — say so
                      // rather than letting it fail with a confusing error.
                      title="Fetch this provider's model list. Requires at least one user to have added a key for it."
                    >
                      {discover.isPending ? "Syncing…" : "Sync models"}
                    </button>
                    <button
                      className="btn-dng text-xs"
                      onClick={() => setDeleting(p)}
                      title="Delete this provider. Also removes every user's keys and models for it."
                    >
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {discover.data?.error && (
          <p className="mt-3 rounded-lg border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-300">
            {discover.data.error}
          </p>
        )}
      </div>

      {/* ── users ── */}
      <div className="card">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="font-medium text-neutral-200">Users</h2>
            <p className="text-xs text-neutral-600">
              There is no signup — you create the user, then mint their key and send it to them.
            </p>
          </div>
          <button className="btn-pri" onClick={() => setCreatingUser(true)}>
            + Create user
          </button>
        </div>

        <table className="w-full">
          <thead>
            <tr>
              <th className="th">ID</th>
              <th className="th">Email</th>
              <th className="th">Role</th>
              <th className="th"></th>
            </tr>
          </thead>
          <tbody>
            {users.data!.users.map((u) => (
              <tr key={u.id}>
                <td className="td tabular-nums text-neutral-500">{u.id}</td>
                <td className="td text-neutral-300">{u.email ?? "—"}</td>
                <td className="td">
                  {u.is_admin ? (
                    <span className="rounded bg-purple-950/50 px-2 py-0.5 text-xs text-purple-400">
                      admin
                    </span>
                  ) : (
                    <span className="text-xs text-neutral-600">user</span>
                  )}
                </td>
                <td className="td text-right">
                  <button
                    className="btn-sec text-xs"
                    onClick={() => mint.mutate(u.id)}
                    disabled={mint.isPending}
                  >
                    Mint gateway key
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {addingProvider && (
        <AddProviderDialog
          onClose={() => setAddingProvider(false)}
          onSaved={() => {
            setAddingProvider(false);
            qc.invalidateQueries({ queryKey: ["admin-providers"] });
          }}
        />
      )}
      {creatingUser && (
        <CreateUserDialog
          onClose={() => setCreatingUser(false)}
          onSaved={() => {
            setCreatingUser(false);
            qc.invalidateQueries({ queryKey: ["admin-users"] });
          }}
        />
      )}
      {minted && <MintedKeyDialog k={minted} onClose={() => setMinted(null)} />}
      {deleting && (
        <DeleteProviderDialog
          provider={deleting}
          pending={remove.isPending}
          onCancel={() => setDeleting(null)}
          onConfirm={() => remove.mutate(deleting.slug)}
        />
      )}
    </div>
  );
}

/**
 * Deleting a provider is destructive AND cross-user: the schema cascades from
 * providers.id, so it also removes every user's keys and models for it. This
 * dialog spells out the blast radius and requires typing the slug to confirm —
 * the same friction GitHub uses for deleting a repo, for the same reason.
 */
function DeleteProviderDialog({
  provider, pending, onCancel, onConfirm,
}: {
  provider: ProviderInfo;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [typed, setTyped] = useState("");
  const matches = typed.trim() === provider.slug;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="card w-full max-w-md border-red-900/60">
        <h2 className="font-medium text-neutral-100">Delete {provider.name}?</h2>
        <p className="mt-2 text-sm text-red-400">
          This is not just your copy. It removes the provider for everyone:
        </p>
        <ul className="mt-2 space-y-1 text-sm text-neutral-400">
          <li>• its {provider.model_count} catalog model{provider.model_count === 1 ? "" : "s"}</li>
          <li>• <strong className="text-neutral-200">every user's API keys</strong> for it</li>
          <li>• all deployments built from those keys</li>
        </ul>
        <p className="mt-2 text-xs text-neutral-600">
          Past usage history is kept. Everything else is gone and cannot be undone.
        </p>

        <label className="label mt-4">
          Type <span className="font-mono text-neutral-300">{provider.slug}</span> to confirm
        </label>
        <input
          className="input font-mono"
          value={typed}
          autoFocus
          onChange={(e) => setTyped(e.target.value)}
        />

        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-sec" onClick={onCancel}>Cancel</button>
          <button
            className="btn-dng"
            disabled={!matches || pending}
            onClick={onConfirm}
          >
            {pending ? "Deleting…" : "Delete provider"}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * The minted key is returned EXACTLY ONCE and is unrecoverable — the server only
 * keeps its hash. So this modal:
 *   • cannot be dismissed by clicking away or pressing Esc,
 *   • requires an explicit "I've copied it" acknowledgement.
 * A toast that auto-dismissed would silently destroy a user's only credential.
 */
function MintedKeyDialog({ k, onClose }: { k: MintedKey; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const [ack, setAck] = useState(false);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4">
      <div className="card w-full max-w-lg border-emerald-900/60">
        <h2 className="font-medium text-neutral-100">Gateway key for user #{k.user_id}</h2>
        <p className="mt-2 text-sm text-amber-400">
          This is the only time this key will ever be shown. It cannot be
          retrieved again — if it's lost, mint a new one.
        </p>

        <div className="mt-4 flex items-center gap-2">
          <code className="flex-1 break-all rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2.5 font-mono text-sm text-emerald-400">
            {k.token}
          </code>
          <button
            className="btn-sec shrink-0"
            onClick={() => {
              navigator.clipboard.writeText(k.token);
              setCopied(true);
            }}
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>

        <label className="mt-5 flex items-center gap-2 text-sm text-neutral-400">
          <input
            type="checkbox"
            checked={ack}
            onChange={(e) => setAck(e.target.checked)}
            className="h-4 w-4 accent-emerald-600"
          />
          I've copied this key and sent it to the user.
        </label>

        <button className="btn-pri mt-4 w-full" disabled={!ack} onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

const CUSTOM = "__custom__";

/**
 * Register a provider — a DESTINATION only. No API key is asked for here.
 *
 *   PRESET  → pick from the dropdown; base_url + LiteLLM prefix come with it.
 *   CUSTOM  → supply slug, name and base_url yourself.
 *
 * Keys live on the Providers & keys page, for admins and users alike. Model
 * auto-discovery still happens — it fires when the FIRST key for this provider
 * is added there, because that key is what discovery calls /v1/models with.
 */
function AddProviderDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const presets = useQuery({ queryKey: ["presets"], queryFn: api.admin.presets });

  const [choice, setChoice] = useState<string>("");
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AddProviderResult | null>(null);

  const isCustom = choice === CUSTOM;
  const preset = presets.data?.presets.find((p) => p.slug === choice);

  const save = useMutation({
    mutationFn: () =>
      api.admin.addProvider(
        isCustom
          ? {
              slug: slug.trim().toLowerCase(),
              name: name.trim(),
              base_url: baseUrl.trim(),
            }
          : { slug: choice },
      ),
    onSuccess: (r) => {
      setError(null);
      setResult(r);
      setTimeout(onSaved, 2200);
    },
    onError: (e: ApiError) => setError(e.message),
  });

  const ready = isCustom
    ? slug.trim() && name.trim() && baseUrl.trim()
    : choice && choice !== "";

  // ── registered: point at the next step, which is where the key goes ──
  if (result) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
        <div className="card w-full max-w-md border-emerald-900/60">
          <h2 className="font-medium text-neutral-100">{result.name} registered</h2>
          <p className="mt-2 text-sm text-neutral-400">{result.detail}</p>
          <button className="btn-sec mt-4 w-full" onClick={onSaved}>Close</button>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="card w-full max-w-md">
        <h2 className="mb-1 font-medium text-neutral-100">Add a provider</h2>
        <p className="mb-4 text-xs text-neutral-600">
          Registers the destination. API keys are added afterwards under
          Providers &amp; keys — the first key auto-discovers its models.
        </p>

        <label className="label">Provider</label>
        <select
          className="input mb-4"
          value={choice}
          onChange={(e) => {
            setChoice(e.target.value);
            setError(null);
          }}
        >
          <option value="" disabled>Choose a provider…</option>
          {presets.data?.presets.map((p) => (
            <option key={p.slug} value={p.slug}>{p.name}</option>
          ))}
          <option value={CUSTOM}>Custom (OpenAI-compatible endpoint)…</option>
        </select>

        {/* PRESET: the endpoint is known, so the admin only pastes a key. */}
        {preset && (
          <div className="mb-4 rounded-lg border border-neutral-800 bg-neutral-950 px-3 py-2.5">
            <div className="font-mono text-xs text-neutral-400">{preset.base_url}</div>
            <div className="mt-1 text-xs text-neutral-600">{preset.hint}</div>
            {preset.docs_url && (
              <a
                href={preset.docs_url}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-block text-xs text-emerald-500 hover:underline"
              >
                Get an API key →
              </a>
            )}
          </div>
        )}

        {/* CUSTOM: we have nowhere to get the endpoint from, so ask for everything. */}
        {isCustom && (
          <>
            <label className="label">Slug</label>
            <input
              className="input mb-1 font-mono"
              placeholder="my-llm"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
            />
            <p className="mb-4 text-xs text-neutral-600">
              Also the LiteLLM prefix — <code>my-llm/some-model</code>.
            </p>

            <label className="label">Display name</label>
            <input
              className="input mb-4"
              placeholder="My LLM"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />

            <label className="label">Base URL</label>
            <input
              className="input mb-1 font-mono"
              placeholder="https://my-llm.example.com/v1"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
            <p className="mb-4 text-xs text-neutral-600">
              Must be OpenAI-compatible — we call <code>{"{base_url}"}/models</code> to
              discover what it serves.
            </p>
          </>
        )}

        {error && (
          <p className="mb-3 rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-sm text-red-400">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button className="btn-sec" onClick={onClose}>Cancel</button>
          <button
            className="btn-pri"
            disabled={!ready || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Adding…" : "Add provider"}
          </button>
        </div>
      </div>
    </div>
  );
}

function CreateUserDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [email, setEmail] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);

  const save = useMutation({
    mutationFn: () =>
      api.admin.createUser({ email: email.trim() || undefined, is_admin: isAdmin }),
    onSuccess: onSaved,
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="card w-full max-w-md">
        <h2 className="mb-4 font-medium text-neutral-100">Create a user</h2>

        <label className="label">Email (optional label)</label>
        <input
          className="input mb-1"
          placeholder="alice@corp.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <p className="mb-4 text-xs text-neutral-600">
          Not a credential — there are no passwords. The gateway key is the login.
        </p>

        <label className="mb-4 flex items-center gap-2 text-sm text-neutral-300">
          <input
            type="checkbox"
            checked={isAdmin}
            onChange={(e) => setIsAdmin(e.target.checked)}
            className="h-4 w-4 accent-emerald-600"
          />
          Make this user an admin
        </label>

        <div className="flex justify-end gap-2">
          <button className="btn-sec" onClick={onClose}>Cancel</button>
          <button className="btn-pri" disabled={save.isPending} onClick={() => save.mutate()}>
            Create
          </button>
        </div>

        <p className="mt-4 text-xs text-neutral-600">
          After creating them, mint a gateway key and send it over — that's how they log in.
        </p>
      </div>
    </div>
  );
}
