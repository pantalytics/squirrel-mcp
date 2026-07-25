import { defineConfig, devices } from "@playwright/test";

// The browser test drives web/console.html (served statically here) against a
// running Squirrel MCP server (default http://localhost:8000/mcp), which the
// Makefile / CI brings up via docker-compose.test.yml.
export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  reporter: process.env.CI ? "list" : [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
  },
  webServer: {
    command: "python3 -m http.server 5173 --directory ../web",
    port: 5173,
    reuseExistingServer: true,
    stdout: "ignore",
    stderr: "pipe",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
