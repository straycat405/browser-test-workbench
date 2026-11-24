import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workbench', ROOT / 'app' / 'server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class WorkbenchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app.DATA = pathlib.Path(cls.tmp.name)
        app.RUNS = app.DATA / 'runs'
        app.SETTINGS = app.DATA / 'settings.json'
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        app.PORT = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:' + str(app.PORT)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, path, data=None, token=True):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Workbench-Token'] = app.TOKEN
        body = json.dumps(data).encode() if data is not None else None
        with urllib.request.urlopen(urllib.request.Request(self.base + path, data=body, headers=headers)) as res:
            return json.load(res)

    def test_config_security_and_real_run(self):
        cfg = {'test_dir': str(ROOT / 'examples'), 'browser': 'chromium', 'headless': True,
               'obsidian_enabled': False, 'obsidian_dir': ''}
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request('/api/settings', cfg, token=False)
        self.assertEqual(err.exception.code, 403)
        with mock.patch.object(app, 'choose_test_folder', return_value=cfg['test_dir']) as picker:
            with self.assertRaises(urllib.error.HTTPError) as err:
                self.request('/api/pick-test-folder', {'path': ''}, token=False)
            self.assertEqual(err.exception.code, 403)
            self.assertEqual(self.request('/api/pick-test-folder', {'path': ''}), {'path': cfg['test_dir']})
            picker.assert_called_once_with('')
        self.assertFalse(self.request('/api/state')['settings']['setup_complete'])
        self.assertTrue(self.request('/api/settings', cfg)['ok'])
        rid = self.request('/api/start', {})['id']
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            data = self.request('/api/run?id=' + rid)
            if data['run']['status'] != 'running':
                break
            time.sleep(.2)
        self.assertEqual(data['run']['status'], 'passed', data)
        self.assertTrue(any(e['type'] == 'step_end' for e in data['events']))
        self.assertTrue(any(a.endswith('.png') for a in data['artifacts']))
        self.assertTrue(app.make_report(app.run_dir(rid)).startswith('# 브라우저 테스트 결과'))
        self.assertIn('<img ', app.report_html(app.run_dir(rid)))
        with self.assertRaises(ValueError):
            app.export_run(app.run_dir(rid))
        vault = app.DATA / 'vault'
        vault.mkdir()
        cfg.update(obsidian_enabled=True, obsidian_dir=str(vault))
        self.request('/api/settings', cfg)
        exported = pathlib.Path(self.request('/api/export', {'id': rid})['path'])
        self.assertTrue((exported / 'report.md').is_file())
        self.assertTrue(list(exported.rglob('*.png')))
        with self.assertRaises(ValueError):
            app.run_dir('../settings.json')
        self.assertFalse(list(ROOT.glob('**/run.json')))


    def test_import_existing_history(self):
        source = app.DATA / 'old-tool'
        run = source / 'out' / '20260101_120000_sample'
        run.mkdir(parents=True)
        result = {'name': '샘플 실행', 'startedAt': '2026-01-01T03:00:00Z',
                  'finishedAt': '2026-01-01T03:00:10Z', 'log': ['예비 단계']}
        (run / 'result.json').write_text(json.dumps(result), encoding='utf-8')
        (run / 'report.md').write_text(
            '## 원본 보고서\n\n| 항목 | 결과 |\n| --- | --- |\n| 화면 조회 | 완료 |\n', encoding='utf-8')
        (run / 'capture.png').write_bytes(b'png fixture')
        log_dir = source / 'dashboard'
        log_dir.mkdir()
        stopped = source / 'out' / '20260101_120100_stopped'
        stopped.mkdir()
        records = [{'type': 'run_start', 'run': stopped.name, 'meta': {'title': '중단된 실행'}},
                   {'type': 'run_end', 'run': stopped.name, 'status': 'aborted', 'error': '중단됨'},
                   {'type': 'run_start', 'run': run.name, 'meta': {'steps': {'legacy': [{'desc': '화면 조회'}]}}},
                   {'type': 'step_end', 'run': run.name, 'side': 'legacy', 'i': 0, 'ms': 10},
                   {'type': 'run_end', 'run': run.name, 'status': 'passed'}]
        (log_dir / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in records), encoding='utf-8')
        self.assertEqual(self.request('/api/import', {'path': str(source)}), {'imported': 2, 'skipped': 0, 'invalid': 0})
        self.assertEqual(self.request('/api/import', {'path': str(source / 'out')})['skipped'], 2)
        imported = next(r for r in app.history() if r.get('source_type') == 'cli_history' and r.get('status') == 'passed')
        self.assertTrue(any(r.get('status') == 'interrupted' for r in app.history()))
        detail = self.request('/api/run?id=' + imported['id'])
        self.assertEqual(imported['status'], 'passed')
        self.assertTrue(any('화면 조회' in e.get('title', '') for e in detail['events']))
        self.assertIn('artifacts/capture.png', detail['artifacts'])
        self.assertIn('원본 보고서', app.report_html(app.run_dir(imported['id'])))
        for name, mime, content in (
            ('artifacts%2Freport.md', 'text/html', '기존 상세 보고서'),
            ('console.txt', 'text/plain', '화면 조회'),
        ):
            url = f"{self.base}/api/file?id={imported['id']}&path={name}"
            with urllib.request.urlopen(url) as response:
                self.assertEqual(response.headers.get_content_type(), mime)
                self.assertIn(content, response.read().decode('utf-8'))
        self.assertTrue((run / 'report.md').is_file())

    def test_project_config_run(self):
        project = app.DATA / 'project'
        project.mkdir()
        (project / 'playwright.config.cjs').write_text(
            "module.exports = {testDir: '.', use: {baseURL: 'http://example.invalid'}};", encoding='utf-8')
        (project / 'config.spec.js').write_text(
            "const {test,expect}=require('@playwright/test'); "
            "test('설정', async ({baseURL}) => { await test.step('주소 확인', async () => "
            "expect(baseURL).toBe('http://example.invalid')); });", encoding='utf-8')
        cfg = {'test_dir': str(project), 'browser': 'chromium', 'headless': True,
               'obsidian_enabled': False, 'obsidian_dir': '',
               'project_config': str(project / 'playwright.config.cjs')}
        self.request('/api/settings', cfg)
        rid = self.request('/api/start', {})['id']
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            data = self.request('/api/run?id=' + rid)
            if data['run']['status'] != 'running':
                break
            time.sleep(.2)
        self.assertEqual(data['run']['status'], 'passed', data)
        self.assertEqual(data['run']['browser'], '프로젝트 설정')
        self.assertTrue(any(event.get('title') == '주소 확인' for event in data['events']))

    def test_stop_running_browser(self):
        folder = app.DATA / 'slow-test'
        folder.mkdir()
        (folder / 'slow.spec.js').write_text(
            "const {test}=require('@playwright/test'); "
            "test('대기', async ({page}) => { await page.goto('data:text/html,대기'); "
            "await test.step('대기 중', async () => page.waitForTimeout(30000)); });", encoding='utf-8')
        cfg = {'test_dir': str(folder), 'browser': 'chromium', 'headless': True,
               'obsidian_enabled': False, 'obsidian_dir': ''}
        self.request('/api/settings', cfg)
        rid = self.request('/api/start', {})['id']
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                data = self.request('/api/run?id=' + rid)
                if any(e.get('title') == '대기 중' and e.get('type') == 'step_begin' for e in data['events']):
                    break
                time.sleep(.2)
            else:
                self.fail('브라우저 대기 단계가 시작되지 않았습니다.')
            with self.assertRaises(urllib.error.HTTPError):
                self.request('/api/stop', {'id': '0' * 32})
            self.assertTrue(self.request('/api/stop', {'id': rid})['ok'])
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                data = self.request('/api/run?id=' + rid)
                if data['run']['status'] != 'running':
                    break
                time.sleep(.2)
            self.assertEqual(data['run']['status'], 'interrupted', data)
            self.assertIsNone(self.request('/api/state')['active'])
        finally:
            if app.ACTIVE == rid and not app.STOP_REQUESTED:
                app.stop_run(rid)

    def test_ui_setup_and_run(self):
        if app.SETTINGS.exists():
            app.SETTINGS.unlink()
        env = os.environ.copy()
        env['BTW_TEST_URL'] = self.base
        env['BTW_IMPORT_DIR'] = str(app.DATA / 'old-tool')
        completed = subprocess.run(['node', str(ROOT / 'tests' / 'ui-smoke.cjs')], cwd=str(ROOT),
                                   env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=75)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == '__main__':
    unittest.main()
