import { defineConfig } from "@playwright/test";

// E2E navigateur de la voie UI (issue #128, friction F5): the API under test
// is the real compose-backed server started by tests/test_ui_e2e.py - the
// base URL and credentials come from the environment, never hard-coded.
// Traces and screenshots are kept on failure as PR evidence.
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.PETRICHOR_UI_BASE_URL ?? "http://127.0.0.1:18090/ui/",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
