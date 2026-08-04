"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { THEME_CONFIG } from "@/shared/constants/appConfig";

interface ThemeState {
  theme: string;
  colorTheme: string;
  customColor: string;
  setTheme: (theme: string) => void;
  setColorTheme: (colorTheme: string) => void;
  setCustomColorTheme: (color: string) => void;
  toggleTheme: () => void;
  initTheme: () => void;
}

const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      theme: THEME_CONFIG.defaultTheme,
      colorTheme: "iris",
      customColor: "#6d4dfb",

      setTheme: (theme) => {
        set({ theme });
        applyTheme(theme);
      },

      setColorTheme: (colorTheme) => {
        set({ colorTheme });
        applyColorTheme(colorTheme, get().customColor);
      },

      setCustomColorTheme: (color) => {
        const normalized = normalizeHexColor(color);
        set({ colorTheme: "custom", customColor: normalized });
        applyColorTheme("custom", normalized);
      },

      toggleTheme: () => {
        const currentTheme = get().theme;
        const newTheme = currentTheme === "dark" ? "light" : "dark";
        set({ theme: newTheme });
        applyTheme(newTheme);
      },

      initTheme: () => {
        const { theme, colorTheme, customColor } = get();
        applyTheme(theme);
        applyColorTheme(colorTheme, customColor);
      },
    }),
    {
      name: THEME_CONFIG.storageKey,
      // v2 = the Iris palette. Anyone still persisting the old default ("coral")
      // never actively picked it, so move them onto the new brand; explicit
      // picks of any other swatch are preserved.
      version: 2,
      migrate: (persisted, version) => {
        const state = persisted as Partial<ThemeState> | undefined;
        if (!state) return state as ThemeState;
        if (version < 2 && state.colorTheme === "coral") {
          return { ...state, colorTheme: "iris" } as ThemeState;
        }
        return state as ThemeState;
      },
    }
  )
);

export const COLOR_THEMES: Record<string, string> = {
  iris: "#6d4dfb",
  blue: "#3b82f6",
  coral: "#e54d5e",
  red: "#f43f5e",
  green: "#10b981",
  violet: "#8b5cf6",
  orange: "#f97316",
  cyan: "#06b6d4",
};

// Apply light/dark theme to document
function applyTheme(theme: string) {
  if (typeof window === "undefined") return;

  const root = document.documentElement;
  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  const effectiveTheme = theme === "system" ? systemTheme : theme;

  if (effectiveTheme === "dark") {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
}

function applyColorTheme(colorTheme: string, customColor: string) {
  if (typeof window === "undefined") return;

  const root = document.documentElement;
  const baseColor =
    colorTheme === "custom"
      ? normalizeHexColor(customColor)
      : COLOR_THEMES[colorTheme] || COLOR_THEMES.iris;

  // Write --brand* rather than --color-primary directly: globals.css reads these
  // through var(--brand, …) so the .dark rule can still swap in the lifted
  // variant. An inline --color-primary would beat every stylesheet rule.
  root.style.setProperty("--brand", baseColor);
  root.style.setProperty("--brand-hover", shadeHexColor(baseColor, -0.14));
  // Dark surfaces are near-black, so the same hue needs lifting to hold contrast.
  const darkBase = shadeHexColor(baseColor, 0.22);
  root.style.setProperty("--brand-dark", darkBase);
  root.style.setProperty("--brand-dark-hover", shadeHexColor(darkBase, 0.16));
  root.style.setProperty("--color-primary-rgb", hexToRgbTriple(baseColor));
}

function hexToRgbTriple(hex: string) {
  const normalized = normalizeHexColor(hex).slice(1);
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  return `${r}, ${g}, ${b}`;
}

function normalizeHexColor(color: string) {
  const value = (color || "").trim();
  const hex = value.startsWith("#") ? value : `#${value}`;
  const valid = /^#([0-9a-fA-F]{6})$/.test(hex);
  return valid ? hex.toLowerCase() : "#6d4dfb";
}

function shadeHexColor(hex: string, percent: number) {
  const normalized = normalizeHexColor(hex).slice(1);
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);

  const shade = (channel: number) => {
    const target = percent < 0 ? 0 : 255;
    const amount = Math.round((target - channel) * Math.abs(percent));
    const next = percent < 0 ? channel - amount : channel + amount;
    return Math.max(0, Math.min(255, next));
  };

  const toHex = (channel: number) => channel.toString(16).padStart(2, "0");
  return `#${toHex(shade(r))}${toHex(shade(g))}${toHex(shade(b))}`;
}

export default useThemeStore;
