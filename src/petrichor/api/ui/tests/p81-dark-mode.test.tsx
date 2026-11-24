import { cleanup, fireEvent, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderAtelier } from "./render-atelier";
import { applyLanguage } from "../src/i18n";
import { THEME_STORAGE } from "../src/theme";
import { authMeResponse } from "./auth-me-mock";

/* P81 (issue #190): dark mode - toggle in the header (data-theme on <html>,
   color-scheme for native controls), persisted in localStorage
   (petrichor.theme), system preference as the default, and dark overrides of
   every color token so no hardcoded white background survives. */

function mockFetchEmpty() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => authMeResponse(String(input)) ?? new Response(JSON.stringify({}), { status: 404 })),
  );
}

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/");
  applyLanguage("fr");
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.style.colorScheme = "";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  applyLanguage("fr");
  document.documentElement.removeAttribute("data-theme");
  localStorage.clear();
});

describe("P81 — mode sombre", () => {
  it("bascule data-theme sur <html>, color-scheme et persiste le choix", async () => {
    mockFetchEmpty();
    await renderAtelier();
    const toggle = screen.getByRole("button", { name: "Basculer en mode sombre / clair" });
    expect(document.documentElement.dataset.theme).not.toBe("dark");
    fireEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE)).toBe("dark");
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(THEME_STORAGE)).toBe("light");
  });

  it("utilise la préférence système (prefers-color-scheme) sans choix persisté", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches: query === "(prefers-color-scheme: dark)",
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      })),
    );
    mockFetchEmpty();
    await renderAtelier();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("le choix persisté prime sur la préférence système", async () => {
    localStorage.setItem(THEME_STORAGE, "light");
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches: query === "(prefers-color-scheme: dark)",
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      })),
    );
    mockFetchEmpty();
    await renderAtelier();
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("surcharge tous les tokens couleur en :root[data-theme=dark] (aucun fond blanc dur)", async () => {
    const styles = readFileSync("src/styles.css", "utf8");
    expect(styles).not.toMatch(/bg-white/);
    const darkBlock = /:root\[data-theme="dark"\] \{([\s\S]*?)\n\}/.exec(styles)?.[1] ?? "";
    for (const token of [
      "--color-ink",
      "--color-muted",
      "--color-line",
      "--color-surface",
      "--color-card",
      "--color-page",
      "--color-primary",
      "--color-error",
      "--color-warning",
      "--color-highlight",
      "--color-banner-error-bg",
      "--color-banner-warning-bg",
    ]) {
      expect(darkBlock).toContain(token + ":");
    }
    expect(darkBlock).toContain("color-scheme: dark");
    // Les pastilles (badges/statuts) ont une variante dark:.
    expect(styles).toContain("@custom-variant dark");
    expect(styles).toMatch(/\.badge-family \{[^}]*dark:bg-indigo-950/);
  });
});
