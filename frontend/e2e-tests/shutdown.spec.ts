/* Copyright 2024 Marimo. All rights reserved. */

import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { getAppUrl, startServer } from "../playwright.config";
import { takeScreenshot } from "./helper";

const _filename = fileURLToPath(import.meta.url);

test("can resume a session", async ({ page }) => {
  const appUrl = getAppUrl("shutdown.py");
  await page.goto(appUrl);

  await expect(page.getByText("'None'", { exact: true })).toBeVisible();
  // type in the form
  await page.locator("#output-Hbol").getByRole("textbox").fill("12345");
  // shift enter to run the form
  await page.keyboard.press("Meta+Enter");

  // wait for the output to appear
  let secondCell = await page.locator(".marimo-cell").nth(1);
  await expect(secondCell.getByText("12345")).toBeVisible();
  await expect(secondCell.getByText("54321")).toBeVisible();

  // Refresh the page
  await page.reload();

  await expect(
    page.getByText("You have reconnected to an existing session."),
  ).toBeVisible();
  secondCell = await page.locator(".marimo-cell").nth(1);
  await expect(page.getByText("12345")).toBeVisible();
  await expect(page.getByText("54321")).toBeVisible();
});

test("restart kernel", async ({ page }) => {
  const appUrl = getAppUrl("shutdown.py");
  await page.goto(appUrl);

  // Wait for page to be fully loaded
  await page.waitForLoadState("networkidle");

  await page.getByTestId("notebook-menu-dropdown").click();
  // Wait for dropdown to be visible and stable
  await page.waitForTimeout(100);

  const restartButton = page.getByRole("menuitem", { name: "Restart kernel" });
  await restartButton.waitFor({ state: "visible" });
  await restartButton.click();

  const confirmButton = page.getByRole("button", { name: "Confirm Restart" });
  await confirmButton.waitFor({ state: "visible" });
  await confirmButton.click();

  await expect(page.getByText("'None'", { exact: true })).toBeVisible();
});

test("shutdown shows disconnected text", async ({ page }) => {
  const appUrl = getAppUrl("shutdown.py");
  await page.goto(appUrl);

  // make changes without saving
  await page
    .getByRole("textbox")
    .filter({ hasText: "import marimo" })
    .locator("div")
    .nth(1)
    .fill("1234");

  // shutdown and confirm
  await page.getByRole("button", { name: "Shutdown" }).click();
  await page.getByRole("button", { name: "Confirm Shutdown" }).click();

  // kernel disconnected message to be on the page
  await expect(page.getByText("kernel not found")).toBeVisible();

  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Download unsaved changes?")).toHaveCount(1);

  await takeScreenshot(page, _filename);
});

test.afterAll(() => {
  startServer("shutdown.py"); // restart the server
});
