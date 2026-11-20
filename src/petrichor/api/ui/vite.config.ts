import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The build output IS the served UI: the API serves /ui/ from ../static
// (spec §9 v1.3, ADR-0101) - no URL change, no Node runtime at serve time.
// Tailwind v4 via its Vite plugin (ADR-0104): dead CSS is purged at build
// time by automatic content detection, the design tokens live in the
// @theme block of styles.css.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/ui/",
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    // Component tests only: e2e/*.spec.ts are Playwright browser tests
    // (issue #128), run by `make ui-e2e`, never by vitest.
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
