import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { api, clearKey, getKey } from "./lib/api";
import KeyGate from "./components/KeyGate";
import { Spinner } from "./components/ui";

import Dashboard from "./pages/Dashboard";
import Providers from "./pages/Providers";
import Models from "./pages/Models";
import Admin from "./pages/Admin";
import Settings from "./pages/Settings";
import Logs from "./pages/Logs";
import Playground from "./pages/Playground";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/models", label: "Models" },
  { to: "/providers", label: "Providers & keys" },
  { to: "/logs", label: "Activity" },
  { to: "/playground", label: "Playground" },
  { to: "/settings", label: "Settings" },
];

export default function App() {
  const hasKey = !!getKey();

  const me = useQuery({
    queryKey: ["me"],
    queryFn: api.me,
    enabled: hasKey,
  });

  if (!hasKey) return <KeyGate onConnect={() => window.location.reload()} />;
  if (me.isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }
  // A 401 already cleared the key and reloaded (see api.ts); anything else here
  // means the server is unreachable, so send them back to the gate rather than
  // rendering a shell full of failed queries.
  if (me.isError || !me.data) return <KeyGate onConnect={() => window.location.reload()} />;

  const user = me.data;

  return (
    <div className="min-h-screen">
      <header className="border-b border-neutral-800 bg-neutral-950/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3">
          <span className="font-semibold text-neutral-100">🛰️ Gateway</span>

          <nav className="flex gap-1">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) =>
                  `rounded-lg px-3 py-1.5 text-sm transition-colors ${
                    isActive
                      ? "bg-neutral-800 text-neutral-100"
                      : "text-neutral-500 hover:text-neutral-300"
                  }`
                }
              >
                {n.label}
              </NavLink>
            ))}

            {/* Cosmetic only. require_admin returns 403 on the server regardless
                of whether this link is rendered. */}
            {user.is_admin && (
              <NavLink
                to="/admin"
                className={({ isActive }) =>
                  `rounded-lg px-3 py-1.5 text-sm transition-colors ${
                    isActive
                      ? "bg-purple-950/60 text-purple-300"
                      : "text-purple-500/70 hover:text-purple-400"
                  }`
                }
              >
                Admin
              </NavLink>
            )}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <span className="text-xs text-neutral-600">{user.email ?? `user #${user.id}`}</span>
            <button
              className="btn-sec text-xs"
              onClick={() => { clearKey(); window.location.reload(); }}
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        <Routes>
          <Route path="/" element={<Dashboard me={user} />} />
          <Route path="/models" element={<Models />} />
          <Route path="/providers" element={<Providers />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/playground" element={<Playground />} />
          <Route path="/settings" element={<Settings />} />
          <Route
            path="/admin"
            element={user.is_admin ? <Admin /> : <Navigate to="/" replace />}
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
