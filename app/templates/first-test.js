const { test, expect } = require('@playwright/test');

// test의 이름은 선택한 실행의 테스트 제목으로 표시됩니다.
test('샘플 화면에서 저장 결과 확인', async ({ page }) => {
  // 실행 단계에는 test.step의 이름과 소요 시간이 표시됩니다.
  await test.step('샘플 화면 열기', async () => {
    // 실제 사이트를 테스트할 때는 이 부분을 page.goto('테스트 주소')로 바꾸세요.
    await page.setContent(`
      <h1>저장 예제</h1>
      <button onclick="document.getElementById('result').hidden = false">저장</button>
      <p id="result" hidden>저장되었습니다</p>
    `);
  });

  await test.step('저장 버튼 누르기', async () => {
    await page.getByRole('button', { name: '저장' }).click();
  });

  await test.step('저장 결과 확인', async () => {
    // expect의 두 번째 인자는 검증 항목에 표시됩니다.
    await expect(page.getByText('저장되었습니다'), '저장 완료 문구가 표시된다').toBeVisible();
    await page.screenshot({ path: test.info().outputPath('result.png') });
  });
});
