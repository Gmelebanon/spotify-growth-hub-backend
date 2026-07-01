import { test, expect } from "@playwright/test";

test("daily website auto sync", async ({ page }) => {
  const baseUrl = process.env.AUTO_SYNC_URL || "https://nerd-engine.vercel.app";
  const username = process.env.AUTO_SYNC_USERNAME || "";
  const password = process.env.AUTO_SYNC_PASSWORD || "";

  if (!username || !password) {
    throw new Error("Missing AUTO_SYNC_USERNAME or AUTO_SYNC_PASSWORD");
  }

  console.log("Opening login page...");

  await page.goto(baseUrl, {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });

  await page.waitForLoadState("networkidle", {
    timeout: 120000,
  });

  await page.locator('input[placeholder="Username"], input[name="username"]').first().fill(username);
  await page.locator('input[placeholder="Password"], input[name="password"], input[type="password"]').first().fill(password);

  await page.locator("button", { hasText: /login/i }).click();

  await page.waitForURL(/dashboard/i, {
    timeout: 120000,
  }).catch(async () => {
    await page.goto(`${baseUrl}/dashboard`, {
      waitUntil: "networkidle",
      timeout: 120000,
    });
  });

  console.log("Dashboard loaded. Looking for Sync All...");

  const syncButton = page.locator('button[data-testid="sync-all-button"]').first();

  await expect(syncButton).toBeVisible({
    timeout: 120000,
  });

  await syncButton.click();

  console.log("Sync All clicked.");

  await page.waitForTimeout(10000);

  await page.screenshot({
    path: "auto-sync-after-click.png",
    fullPage: true,
  });

  console.log("Auto sync completed.");
});