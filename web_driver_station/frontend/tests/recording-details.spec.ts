import { expect, test } from "@playwright/test";

test("a finalized run can be deferred and tagged while video finishes", async ({ page }) => {
  const run = {
    session_id: "5dbc6cc6-d279-4f83-86a0-607d4ff84be6", title: "Field practice", description: "",
    replay_path: "/replays/?recording=5dbc6cc6-d279-4f83-86a0-607d4ff84be6", size_bytes: 100,
    interrupted: false, video_finalizing: true, video_warning: null, upload: { state: "local" },
    needs_details: true, prompt_for_details: true, details_locked: false,
  };
  const expected = { title: run.title, description: "", battery_index: 0, robot_version: 1, opmode_type: "auto" };
  await page.routeWebSocket(/\/ws(?:\/|$)/, socket => socket.close());
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/reviews") {
      await route.fulfill({ json: { runs: [run], upload_configured: false } });
    } else if (path === `/api/reviews/${run.session_id}` && route.request().method() === "PATCH") {
      expect(route.request().postDataJSON()).toEqual(expected);
      Object.assign(run, expected, { needs_details: false, prompt_for_details: false });
      await route.fulfill({ json: expected });
    } else {
      await route.fulfill({ status: 503, json: { detail: "Isolated browser test" } });
    }
  });
  await page.goto("/");

  const prompt = page.getByRole("dialog", { name: "Recording details" });
  await expect(prompt.getByLabel("Battery index")).toHaveValue("");
  await expect(prompt.getByLabel("OpMode type")).toHaveValue("");
  await expect(prompt.getByLabel("Robot version")).toHaveValue("1");
  await expect(prompt.getByRole("button", { name: "Save details" })).toBeDisabled();
  await prompt.getByRole("button", { name: "Fill in later" }).click();
  await expect(prompt).toBeHidden();
  await expect(page.getByText(/Details required.*Finishing video/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Replay", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Details", exact: true }).click();
  await page.getByLabel("Battery index").selectOption("0");
  await page.getByLabel("OpMode type").selectOption("auto");
  await page.getByRole("button", { name: "Save details" }).click();
  await expect(page.getByText("Battery 0 · Robot v1 · OpMode type Auto")).toBeVisible();
  await expect(page.getByText("Recording details saved.")).toBeVisible();
});

test("benchmark storage confirms the details before uploading", async ({ page }) => {
  const runId = "fa4ca768-d9ff-47ae-8747-8b91a49a6dd0";
  const details = { battery_index: 8, robot_version: 1, opmode_type: "debugging" };
  const manifest = { runId, state: "ready", createdAt: "2026-02-10T14:00:00Z", mechanismLabel: "Shooter", storage: { state: "ready" } };
  let saved = false;
  await page.routeWebSocket(/\/ws(?:\/|$)/, socket => socket.close());
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    switch (path) {
      case "/api/debug/status":
        await route.fulfill({ json: { connected: false } });
        break;
      case "/api/debug/runs":
        await route.fulfill({ json: [manifest] });
        break;
      case `/api/debug/results/${runId}`:
        await route.fulfill({ json: { manifest, analysis: null } });
        break;
      case `/api/debug/runs/${runId}/metadata`:
        expect(route.request().method()).toBe("PATCH");
        expect(route.request().postDataJSON()).toMatchObject(details);
        saved = true;
        await route.fulfill({ json: details });
        break;
      case `/api/debug/runs/${runId}/upload`:
        expect(saved).toBe(true);
        expect(route.request().postDataJSON()).toMatchObject({ keep_local: true });
        manifest.storage.state = "complete";
        await route.fulfill({ json: manifest.storage });
        break;
      default:
        await route.fulfill({ status: 503, json: { detail: "Isolated browser test" } });
    }
  });
  await page.goto("/#/debugger");
  await page.getByRole("button", { name: /Shooter/ }).click();
  const dialog = page.getByRole("dialog", { name: "Benchmark recording details" });
  const upload = dialog.getByRole("button", { name: "Upload and keep laptop copy" });
  await expect(dialog.getByLabel("OpMode type")).toHaveValue("debugging");
  await expect(upload).toBeDisabled();
  await dialog.getByLabel("Battery index").selectOption("8");
  await dialog.getByRole("button", { name: "Save details" }).click();
  await expect(upload).toBeEnabled();
  await upload.click();
  await expect(page.getByText("Uploaded successfully. Laptop copy retained.")).toBeVisible();
  await expect(page.getByText("Battery 8 · Robot v1 · OpMode type Debugging")).toBeVisible();
});
