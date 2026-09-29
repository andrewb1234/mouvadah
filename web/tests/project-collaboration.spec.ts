import { test, expect } from "./uiFixture";
import { E2E_API_KEY } from "./authFixture";

test("project invitation, independent workspaces, live downgrade and removal", async ({
  page,
  browser,
}) => {
  const login = await page.request.post("/api/v1/auth/local-session", {
    data: { api_key: E2E_API_KEY },
    headers: { Origin: "http://127.0.0.1:5173" },
  });
  expect(login.ok()).toBeTruthy();
  const shared = await (
    await page.request.post("/api/v1/projects", {
      headers: { Origin: "http://127.0.0.1:5173" },
      data: { name: `Shared delivery ${Date.now()}` },
    })
  ).json();
  const privateProject = await (
    await page.request.post("/api/v1/projects", {
      headers: { Origin: "http://127.0.0.1:5173" },
      data: { name: `Owner private ${Date.now()}` },
    })
  ).json();
  await page.goto(`/app?project=${shared.id}`);
  await page
    .getByRole("button", { name: "People Project access and invitations" })
    .click();
  await page
    .getByLabel(`Invite someone to ${shared.name}`)
    .fill("jordan@example.invalid");
  await page
    .getByRole("button", { name: "Create invitation", exact: true })
    .click();
  const invitationLink = await page
    .getByLabel("Invitation link · copy it now")
    .inputValue();
  await page.evaluate('window.dispatchEvent(new Event("focus"))');
  await expect(page.getByLabel("Invitation link · copy it now")).toHaveValue(
    invitationLink,
  );
  const guest = await browser.newContext({ baseURL: "http://127.0.0.1:5173" });
  try {
    const guestPage = await guest.newPage();
    const guestLogin = await guest.request.post("/api/v1/auth/local-session", {
      data: { api_key: "collaboration_guest_test_only" },
      headers: { Origin: "http://127.0.0.1:5173" },
    });
    expect(guestLogin.ok()).toBeTruthy();
    const personal = await (
      await guest.request.post("/api/v1/projects", {
        headers: { Origin: "http://127.0.0.1:5173" },
        data: { name: `Guest private ${Date.now()}` },
      })
    ).json();
    await guestPage.goto(invitationLink);
    await expect(
      guestPage.getByRole("heading", { name: `Join ${shared.name}` }),
    ).toBeVisible();
    await expect(guestPage).not.toHaveURL(/project_invite/);
    await guestPage.getByRole("button", { name: "Accept invitation" }).click();
    await expect(
      guestPage.getByRole("button", { name: shared.name, exact: true }),
    ).toBeVisible();
    await expect(
      guestPage.getByRole("button", { name: personal.name, exact: true }),
    ).toBeVisible();
    await expect(
      guestPage.getByRole("button", { name: privateProject.name, exact: true }),
    ).toHaveCount(0);
    await expect(
      guestPage.getByRole("button", {
        name: `Delete ${shared.name}`,
        exact: true,
      }),
    ).toHaveCount(0);
    await guestPage
      .getByRole("button", { name: "People Project access and invitations" })
      .click();
    await expect(
      guestPage.getByRole("button", { name: "Create invitation", exact: true }),
    ).toHaveCount(0);
    await page.reload();
    await page
      .getByRole("button", { name: "People Project access and invitations" })
      .click();
    await page.getByLabel("Access for Jordan Lee").selectOption("VIEWER");
    await expect(guestPage.getByText(/Owned by .*View only/)).toBeVisible();
    await expect(
      guestPage.getByRole("button", { name: "New subproject", exact: true }),
    ).toHaveCount(0);
    await guestPage.setViewportSize({ width: 360, height: 800 });
    await expect(
      guestPage.getByRole("heading", { name: "People", exact: true }),
    ).toBeVisible();
    expect(
      await guestPage.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth",
      ),
    ).toBeTruthy();
    const removed = await page.request.delete(
      `/api/v1/projects/${shared.id}/members/2`,
      { headers: { Origin: "http://127.0.0.1:5173" } },
    );
    expect(removed.status()).toBe(204);
    await expect(
      guestPage.getByRole("heading", { name: "Project unavailable" }),
    ).toBeVisible();
    await guestPage.reload();
    await expect(
      guestPage.getByRole("heading", { name: "Project unavailable" }),
    ).toBeVisible();
    expect(
      (await guest.request.get(`/api/v1/projects/${personal.id}`)).status(),
    ).toBe(200);
  } finally {
    await guest.close();
  }
});
