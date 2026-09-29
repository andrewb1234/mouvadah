import { expect, test, type Page } from "./uiFixture";
import { E2E_API_KEY } from "./authFixture";

async function authenticateBrowser(page: Page) {
  const response = await page.request.post("/api/v1/auth/local-session", {
    data: { api_key: E2E_API_KEY },
    headers: { Origin: "http://127.0.0.1:5173" },
  });
  expect(response.ok()).toBeTruthy();
}

test("public landing hands an unauthenticated visitor into sign in", async ({
  page,
}) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "What are you doing?" }),
  ).toBeVisible();
  const documentScroll = (await page.evaluate(
    `({
      shellLocked:
        document.documentElement.classList.contains("app-shell-active"),
      overflow: getComputedStyle(document.documentElement).overflowY,
      scrollHeight: document.documentElement.scrollHeight,
      clientHeight: document.documentElement.clientHeight,
    })`,
  )) as {
    shellLocked: boolean;
    overflow: string;
    scrollHeight: number;
    clientHeight: number;
  };
  expect(documentScroll.shellLocked).toBe(false);
  expect(documentScroll.overflow).not.toBe("hidden");
  expect(documentScroll.scrollHeight).toBeGreaterThan(
    documentScroll.clientHeight,
  );

  await expect(
    page.getByText("Know what every human and agent is doing—and why."),
  ).toBeVisible();
  await expect(
    page.getByText("Mouvadah gives software teams one reviewable record"),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "When a worker disappears, the project does not.",
    }),
  ).toBeVisible();
  await expect(page.getByText("Project-state ledger")).toBeVisible();
  await expect(
    page.getByText("Expired work requeued", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Review accepted; knowledge leaf written", { exact: true }),
  ).toBeVisible();

  await page
    .getByRole("link", { name: "Sign in to your workspace" })
    .click();
  await expect(page).toHaveURL(/\/app$/);
  await expect(page.getByLabel("Local API key")).toBeVisible();
});

test("authenticated root replace-navigates to the application", async ({
  page,
}) => {
  await authenticateBrowser(page);
  await page.goto("/");

  await expect(page).toHaveURL((url) => url.pathname === "/app");
  await expect(
    page.getByRole("heading", { name: "Mouvadah" }),
  ).toBeVisible();
  await expect(page.getByText("Playwright Owner", { exact: true })).toBeVisible();
});

test("authenticated application survives a direct refresh", async ({ page }) => {
  await authenticateBrowser(page);
  await page.goto("/app");
  await expect(page.getByText("Playwright Owner", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(
    (url) => url.pathname === "/app" && Boolean(url.searchParams.get("project")),
  );
  const selectedProjectUrl = page.url();

  await page.reload();
  await expect(page).toHaveURL(selectedProjectUrl);
  await expect(page.getByText("Playwright Owner", { exact: true })).toBeVisible();
});

test("invitation fragments are preserved without becoming visible", async ({
  page,
}) => {
  const invitationToken = "invite-fixture-not-a-secret";
  await page.goto(`/#invite=${invitationToken}`);

  await expect(page).toHaveURL(/\/app$/);
  await expect(page.getByText(invitationToken, { exact: false })).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(
        `window.sessionStorage.getItem("mouvadah.pending-invitation")`,
      ),
    )
    .toBe(invitationToken);
});

test("landing remains contained at 360px", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "What are you doing?" }),
  ).toBeVisible();
  await expect(page.getByText("Shared project state")).toBeVisible();
  await expect(page.getByText("Human + agent control plane")).toHaveCount(0);
  const dimensions = (await page.evaluate(
    `({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    })`,
  )) as { scrollWidth: number; clientWidth: number };
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);

  const proof = await page
    .getByTestId("landing-project-state-ledger")
    .boundingBox();
  expect(proof).not.toBeNull();
  expect(proof!.x).toBeGreaterThanOrEqual(0);
  expect(proof!.x + proof!.width).toBeLessThanOrEqual(360);
});

test("reduced-motion landing exposes the complete project ledger", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");

  await expect(
    page.getByText("Two agents, two owned tickets.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Recovered work becomes durable knowledge.", {
      exact: true,
    }),
  ).toBeVisible();
  const entryAnimations = (await page.evaluate(
    `[...document.querySelectorAll(".motion-enter")].map(
      (element) => getComputedStyle(element).animationName,
    )`,
  )) as string[];
  expect(entryAnimations.every((name) => name === "none")).toBe(true);
});

test("project ledger is static and exposes the complete recovery record", async ({
  page,
}) => {
  await page.goto("/");
  const ledger = page.getByTestId("landing-project-state-ledger");

  await expect(
    ledger.getByText("Last valid heartbeat on #43", { exact: true }),
  ).toBeVisible();
  await expect(
    ledger.getByText("Fresh claim on recovered work", { exact: true }),
  ).toBeVisible();
  await expect(
    ledger.getByText("#42 + #43 → DONE · K-19 → CURRENT", { exact: true }),
  ).toBeVisible();
  await expect(ledger.getByRole("listitem")).toHaveCount(8);
  await expect(ledger.getByRole("button")).toHaveCount(0);
  await expect(ledger.locator("img")).toHaveCount(0);

  const motion = (await ledger.locator("*").evaluateAll((elements) =>
    elements.map((element) => ({
      animationName:
        element.ownerDocument.defaultView?.getComputedStyle(element)
          .animationName ?? "",
      transitionDuration:
        element.ownerDocument.defaultView?.getComputedStyle(element)
          .transitionDuration ?? "",
    })),
  )) as Array<{ animationName: string; transitionDuration: string }>;
  expect(motion.every((item) => item.animationName === "none")).toBe(true);
  expect(motion.every((item) => item.transitionDuration === "0s")).toBe(true);
});

test("project ledger names the recovery boundary without overclaiming", async ({
  page,
}) => {
  await page.goto("/");
  const ledger = page.getByTestId("landing-project-state-ledger");

  await expect(
    ledger.getByText(
      "The expired worker cannot extend its old lease",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    ledger.getByText(
      "A fresh worker must still inspect existing code, external side effects, and evidence",
      { exact: false },
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Inspect the recovery record" }),
  ).toHaveAttribute("href", "#project-state-ledger");
  const labels = await ledger
    .locator("[aria-label]")
    .evaluateAll((elements) =>
      elements.map((element) => element.getAttribute("aria-label")),
    );
  expect(labels).toContain("Project state snapshots");
  expect(labels).toContain("Recovery event ledger");
});

test("landing skip link moves focus to the main content", async ({ page }) => {
  await page.goto("/");
  const skipLink = page.getByRole("link", { name: "Skip to main content" });
  await skipLink.focus();
  await expect(skipLink).toBeFocused();
  await skipLink.press("Enter");
  await expect(page.locator("#landing-main")).toBeFocused();
});
