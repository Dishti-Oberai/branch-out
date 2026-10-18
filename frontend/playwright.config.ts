import { defineConfig, devices } from "@playwright/test";

// Phase 6.2: E2E smoke tests against a real browser + a real (but
// FakeLLM-backed, zero-cost) backend -- see backend/tests/e2e_server.py
// for why this is a full server and not a route-mocked fetch() stub, and
// why it runs on :8010 rather than the default :8000 (never collide with
// a manual testing session's real backend). Both servers are started by
// Playwright itself (`webServer`, an array since 1.28+) and torn down
// after the run; `reuseExistingServer` is false so a stale dev server on
// these ports is never silently reused with stale state.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // every test shares ONE backend session/tree
  workers: 1, // must not run spec files concurrently against that one shared tree
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://localhost:5174",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "python3 tests/e2e_server.py",
      cwd: "../backend",
      url: "http://localhost:8010/session",
      reuseExistingServer: false,
      timeout: 15_000,
    },
    {
      command: "npm run dev -- --port 5174 --strictPort",
      cwd: ".",
      url: "http://localhost:5174",
      reuseExistingServer: false,
      timeout: 15_000,
      env: { VITE_API_URL: "http://localhost:8010" },
    },
  ],
});
