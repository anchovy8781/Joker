import { expect, test } from "@playwright/test";

const uniq = () => `e2e_${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

test("register, see grant, buy on a demo asset, see position and ledger", async ({ page }) => {
  const username = uniq();
  await page.goto("/register");
  await page.getByLabel("아이디").fill(username);
  await page.getByLabel("비밀번호").fill("e2e-password-123");
  await page.getByRole("button", { name: "가입하기" }).click();
  await expect(page.getByText("총자산 (평가)")).toBeVisible();
  await expect(page.getByText("1,000,000").first()).toBeVisible();

  await page.goto("/assets");
  await expect(page.getByText("DEMO 데이터").first()).toBeVisible();
  await page.getByRole("link", { name: /DGAME/ }).click();
  await expect(page.getByRole("heading", { name: "DGAME" })).toBeVisible();

  await page.getByRole("radio", { name: "시장가" }).check({ force: true });
  await page.getByLabel("수량").fill("2");
  await page.getByRole("button", { name: "매수 주문" }).click();
  await expect(page.getByRole("status")).toContainText("체결 완료");

  await page.goto("/portfolio");
  await expect(page.getByRole("cell", { name: /DGAME/ })).toBeVisible();
  await page.getByRole("button", { name: "원장" }).click();
  await expect(page.getByText("가입 지급금")).toBeVisible();
  await expect(page.getByText("매수 대금").first()).toBeVisible();
  await expect(page.getByText("거래 수수료").first()).toBeVisible();
});

test("anonymous users cannot trade and see a login prompt", async ({ page }) => {
  await page.goto("/portfolio");
  await expect(page.getByText("로그인이 필요합니다.")).toBeVisible();
  await page.goto("/assets");
  await page.getByRole("link", { name: /DTECH/ }).click();
  await expect(page.getByRole("link", { name: "로그인 후 거래" })).toBeVisible();
});

test("non-admins cannot use the admin dashboard", async ({ page }) => {
  const username = uniq();
  await page.goto("/register");
  await page.getByLabel("아이디").fill(username);
  await page.getByLabel("비밀번호").fill("e2e-password-123");
  await page.getByRole("button", { name: "가입하기" }).click();
  await expect(page.getByText("총자산 (평가)")).toBeVisible();
  await page.goto("/admin");
  await expect(page.getByText("관리자 권한이 필요합니다.")).toBeVisible();
  const status = await page.evaluate(async () => (await fetch("/api/admin/audit-logs")).status);
  expect(status).toBe(403);
});
