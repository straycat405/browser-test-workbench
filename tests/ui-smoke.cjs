const { chromium } = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  try {
    await page.goto(process.env.BTW_TEST_URL);
    await page.waitForSelector('#settings[open]');
    const response = await page.request.get(new URL('/api/state', page.url()).href);
    const { folders } = await response.json();
    const help = page.getByRole('dialog', { name: '작업대 도움말' });
    await page.locator('#setupHelpButton').click();
    await help.getByRole('heading', { name: '왜 만들었나요?' }).waitFor();
    await help.getByRole('heading', { name: '어떻게 시작하나요?' }).waitFor();
    await help.getByRole('heading', { name: '무엇이 좋아지나요?' }).waitFor();
    await help.getByRole('button', { name: '예제 설정 열기' }).click();
    await help.waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#existingMode').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('#testDir').inputValue(), folders.examples);
    const selectedFolder = path.join(folders.personal, '한글 테스트');
    let pickerCalls = 0;
    await page.route('**/api/pick-test-folder', async route => {
      const initial = route.request().postDataJSON().path;
      assert.equal(initial, pickerCalls ? selectedFolder : folders.examples);
      await route.fulfill({ json: { path: pickerCalls++ ? null : selectedFolder } });
    });
    const pickFolder = page.getByRole('button', { name: '폴더 선택', exact: true });
    await pickFolder.click();
    await page.waitForFunction(value => document.getElementById('testDir').value === value, selectedFolder);
    await pickFolder.click();
    await page.waitForFunction(() => !document.getElementById('pickTestFolder').disabled);
    assert.equal(pickerCalls, 2);
    assert.equal(await page.locator('#testDir').inputValue(), selectedFolder);
    await page.locator('#aiMode').click();
    assert.equal(await page.locator('#testDir').inputValue(), folders.personal);
    await page.locator('#headed').uncheck();
    await page.locator('#settingsForm button[type=submit]').click();
    await page.waitForSelector('#aiGuide[open]');
    assert.equal(await page.locator('#aiTestFolder').inputValue(), folders.personal);
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.locator('#copyAiPrompt').click();
    await page.getByText('요청문을 복사했습니다.', { exact: true }).waitFor();
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    assert.match(copied, /first-test\.spec\.js/);
    assert.match(copied, /\[URL\]/);
    assert.ok(!copied.includes(folders.personal), '공통 요청문에 개인 폴더 경로가 들어 있습니다.');
    await page.locator('#closeAiGuide').click();
    await page.locator('#aiGuideButton').click();
    await page.waitForSelector('#aiGuide[open]');
    await page.locator('#closeAiGuide').click();
    const headerHelp = page.locator('header #aiGuideButton + #helpButton');
    const desktop = page.viewportSize();
    await page.setViewportSize({ width: 390, height: 760 });
    await headerHelp.click();
    assert.equal(await help.evaluate(dialog => {
      const { left, right } = dialog.getBoundingClientRect();
      return left >= 0 && right <= window.innerWidth;
    }), true);
    await page.keyboard.press('Escape');
    await help.waitFor({ state: 'hidden' });
    assert.equal(await headerHelp.evaluate(button => button === document.activeElement), true);
    await page.setViewportSize(desktop);
    await headerHelp.click();
    await help.getByRole('button', { name: 'AI 테스트 안내 열기' }).click();
    await page.waitForSelector('#aiGuide[open]');
    await page.locator('#closeAiGuide').click();
    await headerHelp.click();
    const [helpExampleResponse] = await Promise.all([
      page.waitForResponse(r => r.url().endsWith('/api/start') && r.request().postDataJSON()?.example === true),
      help.getByRole('button', { name: '예제 실행', exact: true }).click(),
    ]);
    assert.equal(helpExampleResponse.ok(), true);
    const { id: helpExampleId } = await helpExampleResponse.json();
    await page.waitForFunction(id => {
      const row = document.querySelector('#runs tr.selected');
      return row?.dataset.run === id && row.textContent.includes('성공');
    }, helpExampleId, { timeout: 45000 });

    await fs.copyFile(path.join(folders.examples, 'example.spec.js'), path.join(folders.personal, 'first-test.spec.js'));
    const [startResponse] = await Promise.all([
      page.waitForResponse(r => r.url().endsWith('/api/start') && r.request().method() === 'POST'),
      page.locator('#start').click(),
    ]);
    const { id } = await startResponse.json();
    await page.waitForFunction(id => document.querySelector('#runs tr.selected')?.dataset.run === id && document.querySelector('#runs tr.selected')?.textContent.includes('성공') && document.querySelector('#current a[href^="/api/report"]')?.href.includes(id), id, { timeout: 45000 });
    const historyButton = page.locator('#runs tr.selected button[data-id]');
    await historyButton.focus();
    await page.keyboard.press('Enter');
    await page.waitForResponse(r => r.url().endsWith('/api/state'), { timeout: 6000 });
    assert.equal(await historyButton.getAttribute('aria-pressed'), 'true');
    assert.equal(await historyButton.evaluate(element => element === document.activeElement), true);
    await page.waitForSelector('#gallery img', { timeout: 10000 });
    await page.locator('#gallery .preview-trigger').first().click();
    await page.locator('.preview-dialog[open] img').waitFor();
    await page.mouse.click(5, 5);
    if (await page.locator('.preview-dialog').evaluate(el => el.open)) throw Error('대시보드의 바깥 영역으로 미리보기를 닫지 못했습니다.');
    await page.locator('#gallery .preview-trigger').first().click();
    await page.locator('.preview-close').click();
    if (await page.locator('.preview-dialog').evaluate(el => el.open)) throw Error('대시보드의 닫기 버튼이 동작하지 않습니다.');
    const reportLink = page.locator('#current').getByRole('link', { name: '보고서', exact: true });
    const [report] = await Promise.all([
      page.context().waitForEvent('page'),
      reportLink.click(),
    ]);
    await report.waitForLoadState();
    if (await report.locator('h1').textContent() !== '테스트 결과 보고서') throw Error('보고서 제목이 표시되지 않습니다.');
    if (await report.locator('#execution-log').count()) throw Error('보고서에 로그 화면이 함께 표시됩니다.');
    const image = report.locator('img').first();
    await image.waitFor({timeout:5000}).catch(async e => { console.error('보고서 URL:', report.url()); console.error('보고서 본문:', await report.locator('body').innerText()); throw e; });
    await report.waitForFunction(() => document.querySelector('.gallery img')?.naturalWidth > 0);
    await report.locator('.gallery .preview-trigger').first().click();
    await report.locator('.preview-dialog[open] img').waitFor();
    await report.locator('.preview-close').click();
    if (await report.locator('.preview-dialog').evaluate(el => el.open)) throw Error('보고서의 닫기 버튼이 동작하지 않습니다.');
    await report.locator('.gallery .preview-trigger').first().click();
    await report.mouse.click(5, 5);
    if (await report.locator('.preview-dialog').evaluate(el => el.open)) throw Error('보고서의 바깥 영역으로 미리보기를 닫지 못했습니다.');
    await report.locator('.gallery .preview-trigger').first().click();
    await report.keyboard.press('Escape');
    if (await report.locator('.preview-dialog').evaluate(el => el.open)) throw Error('Esc로 미리보기를 닫지 못했습니다.');
    if (!(await report.locator('body').innerText()).includes('성공')) throw Error('보고서 성공 상태가 표시되지 않습니다.');
    await report.close();
    const steps = await page.locator('#current .event').allTextContents();
    if (steps.filter(text => text.includes('제목과 본문 확인')).length !== 1) throw Error('완료한 단계가 중복 표시됩니다.');
    const openRunTab = async label => {
      const [tab] = await Promise.all([
        page.context().waitForEvent('page'),
        page.locator('#current').getByRole('link', { name: label, exact: true }).click(),
      ]);
      return tab;
    };
    const sampleLog = await openRunTab('실행 로그');
    if (new URL(sampleLog.url()).pathname !== '/api/log' || await sampleLog.title() !== '실행 로그') throw Error('실행 로그가 별도 화면에서 열리지 않습니다.');
    if (!(await sampleLog.locator('#execution-log pre').innerText()).includes('제목과 본문 확인')) throw Error('빈 콘솔의 실행 단계가 표시되지 않습니다.');
    await sampleLog.close();
    await fs.writeFile(path.join(folders.personal, 'slow.spec.js'),
      "const {test}=require('@playwright/test'); test('중단 시험', async ({page}) => { await page.goto('data:text/html,대기'); await test.step('대기 중', () => page.waitForTimeout(30000)); });");
    const [slowResponse] = await Promise.all([
      page.waitForResponse(r => r.url().endsWith('/api/start') && r.request().method() === 'POST'),
      page.locator('#start').click(),
    ]);
    const slowRun = (await slowResponse.json()).id;
    await page.getByText('▶ 대기 중', { exact: true }).waitFor({ timeout: 30000 });
    await page.getByRole('button', { name: '실행 중단', exact: true }).click();
    await page.waitForFunction(id => {
      const row = document.querySelector('#runs tr.selected');
      return row?.dataset.run === id && row.textContent.includes('중단');
    }, slowRun, { timeout: 15000 });
    assert.equal(await page.locator('#stop').isHidden(), true);
    await fs.unlink(path.join(folders.personal, 'slow.spec.js'));
    await fs.writeFile(path.join(folders.personal, 'fail.spec.js'),
      "const {test,expect}=require('@playwright/test'); test('실패 시험', async () => { await test.step('값 확인', async () => expect(1).toBe(2)); });");
    const [failedResponse] = await Promise.all([
      page.waitForResponse(r => r.url().endsWith('/api/start') && r.request().method() === 'POST'),
      page.locator('#start').click(),
    ]);
    const failedRun = (await failedResponse.json()).id;
    await page.waitForFunction(id => {
      const row = document.querySelector('#runs tr.selected');
      return row?.dataset.run === id && row.textContent.includes('실패');
    }, failedRun, { timeout: 30000 });
    await page.getByText('실패 위치 · 값 확인').waitFor();
    await page.getByText('오류 상세와 캡처를 확인한 뒤 실패한 조건을 살펴보세요.').waitFor();
    await page.locator('#importButton').click();
    await page.locator('#importPath').fill(process.env.BTW_IMPORT_DIR);
    await page.locator('#importSubmit').click();
    await page.waitForFunction(() => document.querySelector('#importMessage')?.textContent.includes('기존 기록 2건'));
    await page.locator('#importCancel').click();
    await page.locator('#runs tr').getByRole('button', { name: '가져온 기록 · 샘플 실행' }).click();
    await page.locator('#current').getByText('가져온 기록 · 샘플 실행', { exact: false }).first().waitFor();
    if (await page.locator('#gallery').getByRole('link', { name: 'report.md' }).count()) throw Error('기존 보고서 원문 링크가 중복 표시됩니다.');
    const importedLog = await openRunTab('실행 로그');
    if (!(await importedLog.locator('#execution-log pre').innerText()).includes('화면 조회')) throw Error('가져온 기록의 실행 로그가 표시되지 않습니다.');
    await importedLog.close();
    const importedReport = await openRunTab('보고서');
    if (!(await importedReport.locator('#imported-report table').innerText()).includes('화면 조회')) throw Error('가져온 보고서 표가 표시되지 않습니다.');
    await importedReport.close();
    await page.locator('#settingsButton').click();
    await page.waitForSelector('#settings[open]');
    if (await page.locator('#obsidian').isChecked()) throw Error('Obsidian은 기본적으로 꺼져 있어야 합니다.');
    console.log('초기 설정, 실행 이력, 보고서, 캡처, 상세 설정 정상');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
