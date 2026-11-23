const { test, expect } = require('@playwright/test');

test('웹 페이지 제목 확인', async ({ page }) => {
  await test.step('샘플 페이지 열기', async () => {
    await page.goto('data:text/html,<title>Example Domain</title><h1>Example Domain</h1>');
  });
  await test.step('제목과 본문 확인', async () => {
    await expect(page).toHaveTitle(/Example Domain/);
    await expect(page.locator('h1')).toHaveText('Example Domain');
    await page.screenshot({ path: test.info().outputPath('sample.png') });
  });
});
