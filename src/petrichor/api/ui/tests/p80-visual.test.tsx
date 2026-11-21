import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/* P80 (issue #189) guards: the visual direction lives in color tokens (so the
   P81 dark mode can override them), no hardcoded white background survives,
   and every palette entry documents a WCAG AA contrast ratio >= 4.5:1. */

// vitest runs with the UI package root as cwd.
const styles = readFileSync("src/styles.css", "utf8");

describe("P80 — direction visuelle", () => {
  it("n'a aucun fond blanc codé en dur (les tokens seront surchargés en sombre)", () => {
    expect(styles).not.toMatch(/bg-white/);
    expect(styles).toContain("--color-card:");
    expect(styles).toContain("--color-page:");
  });

  it("déclare le bandeau en dégradé indigo→violet→cyan et les boutons primaires en dégradé", () => {
    expect(styles).toContain("from-header-a via-header-b to-header-c");
    expect(styles).toContain("from-accent-a to-accent-b");
  });

  it("documente un contraste WCAG AA (>= 4.5:1) pour chaque entrée de palette texte", () => {
    const lines = styles.match(/--color-[a-z-]+: #[0-9a-f]{6};[^\n]*/g) ?? [];
    const withRatio = lines
      .map((line) => Number(/([0-9]+\.[0-9]+):1/.exec(line)?.[1]))
      .filter((ratio) => Number.isFinite(ratio));
    // Every text/background palette entry documents its ratio; borders
    // (--color-line) carry none and are exempt.
    expect(withRatio.length).toBeGreaterThanOrEqual(10);
    for (const ratio of withRatio) {
      expect(ratio).toBeGreaterThanOrEqual(4.5);
    }
  });
});
