import { expect, test } from "@playwright/test";

test("laptop launch, motor arm, expiry, and blur require explicit control", async ({ page }) => {
  const state = {
    connected: false, selected_session: "",
    sessions: [
      { session_id: "a", robot_id: "Hub A", opmode: "Debugger", protocol: 3 },
      { session_id: "b", robot_id: "Hub B", opmode: "Debugger", protocol: 3 },
    ],
    manifest: { revision: 1, nodes: [
      { id: "motor", parentId: "", label: "Registered mechanism", kind: "debug_folder", enabled: true },
      { id: "motor.power", parentId: "motor", label: "Simple Motor Power", kind: "debug_tool", enabled: true },
    ] },
    safety: { armed: false }, tool_ready: null as object | null,
  };
  let renewals = 0, releases = 0, arms = 0, launches = 0, failRenewal = false;
  const pageErrors: string[] = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  // Every API/websocket is intercepted: this test cannot contact a robot backend.
  await page.routeWebSocket(/\/ws(?:\/|$)/, socket => socket.close());
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let result: unknown = {};
    if (!path.startsWith("/api/debug/")) {
      await route.fulfill({ status: 503, json: { detail: "Isolated browser test" } }); return;
    }
    if (path.endsWith("/status")) result = state;
    if (path.endsWith("/runs")) result = [];
    if (path.endsWith("/launch")) {
      expect(route.request().postDataJSON().name).toBe("FTC Advanced Debugger");
      launches++;
    }
    if (path.endsWith("/target")) {
      state.selected_session = route.request().postDataJSON().session_id;
      state.connected = true; result = state;
    }
    if (path.endsWith("/select")) state.tool_ready = {
      nodeId: "motor.power", toolInstanceId: "instance-1",
      parameters: [{ id: "motor.power", min: 0, max: .25, writable: true }],
    };
    if (path.endsWith("/control/acquire")) result = { lease_id: "test-owner" };
    if (path.endsWith("/control/arm")) {
      expect(route.request().postDataJSON().lease_id).toBe("test-owner");
      arms++; state.safety.armed = true;
    }
    if (path.endsWith("/control/renew")) {
      renewals++;
      if (failRenewal) { await route.fulfill({ status: 409, json: { detail: "Lease expired" } }); return; }
    }
    if (path.endsWith("/control/release")) { releases++; state.safety.armed = false; }
    await route.fulfill({ json: result });
  });
  await page.goto("/#/debugger");
  await page.getByRole("button", { name: "Start debugger from laptop" }).click();
  await expect.poll(() => launches).toBe(1);
  await expect(page.getByRole("button", { name: "Acquire debugger control" })).toBeDisabled();
  await page.getByRole("combobox", { name: "Robot session" }).selectOption("a");
  await page.getByRole("button", { name: "Select tool" }).click();
  await expect(page.getByRole("button", { name: "Acquire debugger control" })).toBeEnabled();
  await page.getByRole("button", { name: "Acquire debugger control" }).click();
  await expect(page.getByRole("combobox", { name: "Robot session" })).toBeDisabled();
  await expect(page.getByText("Observation only", { exact: true })).toBeVisible();
  await expect(page.getByRole("slider", { name: "Motor power" })).toHaveCount(0);
  await expect.poll(() => renewals).toBeGreaterThan(1);
  await page.getByRole("button", { name: "Arm selected motor" }).click();
  await expect.poll(() => arms).toBe(1);
  await expect(page.getByText("Armed", { exact: true })).toBeVisible();
  await expect(page.getByRole("slider", { name: "Motor power" })).toHaveAttribute("min", "0");
  await expect(page.getByRole("slider", { name: "Motor power" })).toHaveAttribute("max", "0.25");
  // Simulate the browser's native blur event, independent of page cleanup/navigation.
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await expect.poll(() => releases).toBe(1);
  await expect(page.getByRole("button", { name: "Acquire debugger control" })).toBeEnabled();
  await expect(page.getByText("Observation only", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Robot session" }).selectOption("b");
  await expect.poll(() => state.selected_session).toBe("b");
  await page.getByRole("button", { name: "Acquire debugger control" }).click();
  failRenewal = true;
  await expect(page.getByRole("button", { name: "Acquire debugger control" })).toBeEnabled();
  await expect(page.getByText("Observation only", { exact: true })).toBeVisible();
  expect(pageErrors).toEqual([]);
});
