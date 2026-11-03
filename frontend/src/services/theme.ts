import { useEffect, useState, useCallback } from "react";

export type ThemeMode = "dark" | "light" | "system";
export type ResolvedTheme = "dark" | "light";

export const THEME_STORAGE_KEY = "genaro_pos_theme_pref";

export function getSavedThemeMode(): ThemeMode {
  if (typeof window === "undefined") return "dark";
  try {
    const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (saved === "dark" || saved === "light" || saved === "system") {
      return saved;
    }
  } catch {
    // Ignore storage errors
  }
  return "dark";
}

export function getSystemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return "dark";
  }
  try {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function resolveTheme(mode: ThemeMode): ResolvedTheme {
  if (mode === "system") {
    return getSystemTheme();
  }
  return mode;
}

export function applyThemeToDOM(mode: ThemeMode): ResolvedTheme {
  const resolved = resolveTheme(mode);
  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("data-theme", resolved);
    document.documentElement.setAttribute("data-theme-mode", mode);
    document.documentElement.style.colorScheme = resolved;
    const metaTheme = document.querySelector('meta[name="theme-color"]');
    if (metaTheme) {
      metaTheme.setAttribute("content", resolved === "dark" ? "#101A2B" : "#F4F1EC");
    }
  }
  return resolved;
}

export function useTheme() {
  const [themeMode, setThemeModeState] = useState<ThemeMode>(() => getSavedThemeMode());
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() =>
    resolveTheme(getSavedThemeMode())
  );

  const setThemeMode = useCallback((newMode: ThemeMode) => {
    setThemeModeState(newMode);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, newMode);
    } catch {
      // Ignore storage errors
    }
    const nextResolved = applyThemeToDOM(newMode);
    setResolvedTheme(nextResolved);
  }, []);

  useEffect(() => {
    const nextResolved = applyThemeToDOM(themeMode);
    setResolvedTheme(nextResolved);

    if (themeMode !== "system" || typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }

    let mql: MediaQueryList | null = null;
    const handleChange = () => {
      const updated = applyThemeToDOM("system");
      setResolvedTheme(updated);
    };

    try {
      mql = window.matchMedia("(prefers-color-scheme: light)");
      if (mql.addEventListener) {
        mql.addEventListener("change", handleChange);
      } else if (mql.addListener) {
        mql.addListener(handleChange);
      }
    } catch {
      mql = null;
    }

    return () => {
      if (mql) {
        if (mql.removeEventListener) {
          mql.removeEventListener("change", handleChange);
        } else if (mql.removeListener) {
          mql.removeListener(handleChange);
        }
      }
    };
  }, [themeMode]);

  return {
    themeMode,
    resolvedTheme,
    setThemeMode,
  };
}
