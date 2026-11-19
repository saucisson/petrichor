import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build output IS the served UI: the API serves /ui/ from ../static
// (spec §9 v1.3, ADR-0101) - no URL change, no Node runtime at serve time.
export default defineConfig({
  plugins: [react()],
  base: "/ui/",
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
  },
});
