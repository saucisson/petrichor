/* Theme (P81, issue #190): light/dark via data-theme on <html>. On load the
   persisted choice wins; otherwise the system preference
   (prefers-color-scheme) decides. The choice is persisted in localStorage
   under petrichor.theme. All colors come from the @theme tokens overridden
   in :root[data-theme="dark"] - no hardcoded background survives. */

export const THEME_STORAGE = "petrichor.theme";
export const SUPPORTED_THEMES = ["light", "dark"] as const;
export type Theme = (typeof SUPPORTED_THEMES)[number];
export const DEFAULT_THEME: Theme = "light";

export function isTheme(value: string | null): value is Theme {
  return value !== null && (SUPPORTED_THEMES as readonly string[]).includes(value);
}

export function storedTheme(): Theme | null {
  const stored = localStorage.getItem(THEME_STORAGE);
  return isTheme(stored) ? stored : null;
}

export function systemTheme(): Theme {
  // jsdom and very old engines lack matchMedia: fall back to light.
  if (typeof window.matchMedia !== "function") return DEFAULT_THEME;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function initialTheme(): Theme {
  return storedTheme() ?? systemTheme();
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  localStorage.setItem(THEME_STORAGE, theme);
}

/* Set once at module load so the first paint already carries the theme. */
export function applyInitialTheme(): void {
  const theme = initialTheme();
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}
