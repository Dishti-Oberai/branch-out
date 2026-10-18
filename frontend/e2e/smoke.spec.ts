import { expect, test } from "@playwright/test";

const API = "http://localhost:8010";

/**
 * Phase 6.2: end-to-end smoke test over a real browser + a real, but
 * FakeLLM-backed (zero real-API-cost) backend -- see
 * backend/tests/e2e_server.py. All tests in this file share ONE backend
 * session (Playwright's config pins `workers: 1`), so assertions use
 * RELATIVE node-count deltas throughout, never an absolute count -- an
 * earlier test's nodes are still there when a later one runs, and
 * `page.reload()` re-fetches the already-persisted backend session (it's
 * not an empty tree; nothing in this app resets backend state on a fresh
 * page load, by design).
 *
 * The 50-node performance check deliberately runs LAST in this same file
 * (declaration order within a file is preserved even with workers: 1) --
 * it seeds dozens of extra nodes into this shared session, which must
 * never happen before the functional tests above still expect a small,
 * predictable tree shape.
 */
test("branch, pick, backtrack, save, reload — the core tree milestone", async ({ page }) => {
  await page.goto("/");
  const start = await page.locator(".bo-node").count();

  // Explore 3 approaches from root.
  await page.getByPlaceholder(/Ask for one answer/).fill("write a binary search");
  await page.getByRole("button", { name: "Explore 3 approaches" }).click();
  await expect(page.locator(".bo-node")).toHaveCount(start + 3, { timeout: 10_000 });

  // Pick one of the 3 new branches — index `start` (0-indexed), the first
  // newly-appended node. NOT `.bo-node.normal` generically: that class
  // also matches Root whenever it isn't currently the active node.
  await page.locator(".bo-node").nth(start).click();
  await expect(page.locator(".inspector")).toBeVisible();

  // "Make active" backtracks the tree's cursor onto it.
  await page.getByRole("button", { name: /Make active/ }).click();
  await expect(page.locator(".bo-node.active")).toHaveCount(1);

  // Ask a single follow-up (not a branch) — exactly one new node appears.
  // Scoped to the prompt bar: NodeInspector's own query form also has a
  // same-labeled "Ask" submit button for querying a past node.
  await page.getByPlaceholder(/Ask for one answer/).fill("add a docstring");
  await page.locator(".prompt-bar").getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.locator(".bo-node")).toHaveCount(start + 4, { timeout: 10_000 });

  // Backtrack to root: the whole branch becomes orphaned (dashed/dim).
  await page.locator(".bo-node").filter({ hasText: "Root" }).click();
  await expect(page.locator(".bo-node.orphaned").first()).toBeVisible();

  // Save succeeds (no error banner), and the backend session persists
  // across a fresh page load — GET /session on mount re-fetches exactly
  // what's there, not an empty tree.
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.locator(".error")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".bo-node")).toHaveCount(start + 4, { timeout: 10_000 });
});

test("merging two sibling branches creates one new synthesized node", async ({ page }) => {
  await page.goto("/");
  const start = await page.locator(".bo-node").count();

  await page.getByPlaceholder(/Ask for one answer/).fill("write a quicksort");
  await page.getByRole("button", { name: "Explore 3 approaches" }).click();
  await expect(page.locator(".bo-node")).toHaveCount(start + 3, { timeout: 10_000 });

  // New nodes are always appended (backend dict preserves insertion
  // order), so the first of these 3 new siblings sits at index `start`
  // (0-indexed) — NOT `.bo-node.normal` generically, which also matches
  // Root itself whenever it isn't the active node (Root has no parent,
  // so it has no siblings and never shows the merge UI, causing a timeout).
  await page.locator(".bo-node").nth(start).click();
  await page.getByText("State", { exact: true }).click(); // merge UI lives in the State tab
  await page.getByRole("combobox").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Synthesize combined approach" }).click();
  await expect(page.locator(".bo-node")).toHaveCount(start + 4, { timeout: 10_000 });
});

test("Escape closes the inspector", async ({ page }) => {
  await page.goto("/");
  await page.locator(".bo-node").first().click();
  await expect(page.locator(".inspector")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".inspector")).not.toBeVisible();
});

test("tree with 50+ nodes loads and renders within a generous time budget", async ({ page, request }) => {
  // Phase 6.2: "test performance at 50 nodes" from the ROADMAP's risk
  // register — backend/tests/test_performance.py covers the tree-state/
  // serialization side; this is the frontend render path. Seeds via the
  // API directly (fast, single-LLM-call /continue, no fan-out) rather
  // than clicking through the UI 50 times. Runs LAST in this file on
  // purpose — see the file-level comment above.
  const before = (await (await request.get(`${API}/session`)).json()).nodes.length;
  let activeId = "root";
  const needed = Math.max(0, 50 - before);
  for (let i = 0; i < needed; i++) {
    const res = await request.post(`${API}/continue`, {
      data: { node_id: activeId, prompt: `seed node ${i}` },
    });
    const { node } = await res.json();
    activeId = node.id;
  }

  const start = Date.now();
  await page.goto("/");
  await expect(page.locator(".bo-node")).toHaveCount(before + needed, { timeout: 15_000 });
  const elapsed = Date.now() - start;

  expect(elapsed).toBeLessThan(8_000);
});
