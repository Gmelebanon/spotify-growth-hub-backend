import { test, expect } from "@playwright/test";

test("daily website auto sync", async ({ page }) => {
  const baseUrl = process.env.AUTO_SYNC_URL || "https://nerd-engine.vercel.app";
  const path = process.env.AUTO_SYNC_PATH || "/";
  const buttonText = process.env.AUTO_SYNC_BUTTON_TEXT || "Sync All";
  const successText = process.env.AUTO_SYNC_SUCCESS_TEXT || "Sync completed";

  await page.goto(`${baseUrl}${path}`, {
    waitUntil: "networkidle",
    timeout: 120000,
  });

  await page.getByRole("button", { name: buttonText }).click({
    timeout: 120000,
  });

  await expect(page.getByText(new RegExp(successText, "i"))).toBeVisible({
    timeout: 180000,
  });
});