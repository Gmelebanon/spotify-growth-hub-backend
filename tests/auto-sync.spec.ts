import { test, expect } from "@playwright/test";

// Optional fallback UI test. The GitHub workflow now uses direct backend POST calls,
// which is more reliable for scheduled syncs. Keep this file only if you still want
// to test the website Sync All button manually.
test("website Sync All button waits for backend response", async ({ page }) => {
  const baseUrl = process.env.AUTO_SYNC_URL || "https://nerd-engine.vercel.app";
  const username = process.env.AUTO_SYNC_USERNAME || "";
  const password = process.env.AUTO_SYNC_PASSWORD || "";

  if (!username || !password) {
    throw new Error("Missing AUTO_SYNC_USERNAME or AUTO_SYNC_PASSWORD");
  }

  await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForLoadState("networkidle", { timeout: 120000 });

  await page.locator('input[placeholder="Username"], input[name="username"]').first().fill(username);
  await page.locator('input[placeholder="Password"], input[name="password"], input[type="password"]').first().fill(password);
  await page.locator("button", { hasText: /login/i }).click();

  await page.waitForURL(/dashboard/i, { timeout: 120000 }).catch(async () => {
    await page.goto(`${baseUrl}/dashboard`, { waitUntil: "networkidle", timeout: 120000 });
  });

  const syncButton = page.locator('button[data-testid="sync-all-button"]').first();
  await expect(syncButton).toBeVisible({ timeout: 120000 });

  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/playlists/sync-all") &&
      response.request().method() === "POST",
    { timeout: 900000 }
  );

  await syncButton.click();
  const response = await responsePromise;
  const body = await response.text();

  console.log("Sync All HTTP status:", response.status());
  console.log("Sync All response:", body);

  expect(response.ok(), body).toBeTruthy();
});
