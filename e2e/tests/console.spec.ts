import { test, expect } from "@playwright/test";

const MCP_URL = process.env.SQUIRREL_MCP_URL ?? "http://localhost:8000/mcp";

// Skip cleanly if the MCP server stack isn't up (e.g. someone ran the browser
// test without `docker compose -f docker-compose.test.yml up`).
test.beforeAll(async () => {
  try {
    const resp = await fetch(MCP_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 0,
        method: "initialize",
        params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "probe", version: "0" } },
      }),
    });
    test.skip(!resp.ok, `Squirrel MCP server not reachable at ${MCP_URL} (${resp.status})`);
  } catch (e) {
    test.skip(true, `Squirrel MCP server not reachable at ${MCP_URL}: ${e}`);
  }
});

test("browser client connects, lists tools, and reads folders from the live server", async ({ page }) => {
  await page.goto("/console.html");

  // Point the console at the MCP server and connect.
  await page.locator("#url").fill(MCP_URL);
  await page.getByRole("button", { name: "Connect & list tools" }).click();

  // Handshake succeeded and tools were listed.
  await expect(page.locator("#status")).toHaveAttribute("data-state", "connected");
  await expect(page.locator('.tool[data-name="mail_search"]')).toBeVisible();
  await expect(page.locator('.tool[data-name="mail_send"]')).toBeVisible();

  // Call a real tool -> real folders from the backing mailbox.
  await page.getByRole("button", { name: "Call mail_list_folders" }).click();
  await expect(page.locator("#status")).toHaveAttribute("data-state", "done");
  await expect(page.locator('.folder[data-name="INBOX"]')).toBeVisible();
});
