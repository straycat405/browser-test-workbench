const { test, expect } = require('@playwright/test');

test('웹 페이지 제목 확인', async ({ page }) => {
  const sample = 'https://workbench-example.invalid';
  await page.route(sample + '/', route => route.fulfill({
    contentType: 'text/html; charset=utf-8',
    body: `<title>Example Domain</title><button id="load">결과 불러오기</button><h1></h1>
      <script>
        document.getElementById('load').onclick = async () => {
          const response = await fetch('/api/result');
          document.querySelector('h1').textContent = (await response.json()).title;
        };
      </script>`,
  }));
  await page.route(sample + '/api/result', route => route.fulfill({ json: { title: 'Example Domain' } }));
  await test.step('샘플 페이지 열기', async () => {
    await page.goto(sample);
  });
  await test.step('제목과 본문 확인', async () => {
    await expect(page, '페이지 제목에 Example Domain이 표시된다').toHaveTitle(/Example Domain/);
    await test.step('요청부터 화면 표시까지', async () => {
      await Promise.all([
        page.waitForResponse(sample + '/api/result'),
        page.getByRole('button', { name: '결과 불러오기' }).click(),
      ]);
      await expect(page.locator('h1'), '본문 제목이 Example Domain과 일치한다').toHaveText('Example Domain');
    });
    await page.screenshot({ path: test.info().outputPath('sample.png') });
  });
});
