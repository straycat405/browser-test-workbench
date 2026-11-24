#!/usr/bin/env python3
"""웹 테스트 작업대: 각 PC에서 실행하는 로컬 전용 서버."""
import json
import html
import datetime
import os
import pathlib
import re
import signal
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
if os.name == 'nt':
    DATA = pathlib.Path(os.environ.get('LOCALAPPDATA', pathlib.Path.home() / 'AppData' / 'Local')) / 'BrowserTestWorkbench'
elif sys.platform == 'darwin':
    DATA = pathlib.Path.home() / 'Library' / 'Application Support' / 'BrowserTestWorkbench'
else:
    DATA = pathlib.Path(os.environ.get('XDG_DATA_HOME', pathlib.Path.home() / '.local' / 'share')) / 'browser-test-workbench'
DATA = pathlib.Path(os.environ.get('BTW_DATA_DIR', str(DATA))).expanduser().resolve()
RUNS = DATA / 'runs'
SETTINGS = DATA / 'settings.json'
PORT = int(os.environ.get('BTW_PORT', '7790'))
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
FOLDER_PICKER_LOCK = threading.Lock()
ACTIVE = None
ACTIVE_PROCESS = None
STOP_REQUESTED = False
DEFAULT = {'test_dir': str(ROOT / 'examples'), 'browser': 'chromium', 'headless': False,
           'obsidian_enabled': False, 'obsidian_dir': '', 'project_config': '', 'setup_complete': False}


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def settings():
    saved = read_json(SETTINGS, {})
    return {**DEFAULT, **saved} if isinstance(saved, dict) else DEFAULT.copy()


def validate_settings(data, *, create_personal_dir=False):
    if not isinstance(data, dict):
        raise ValueError('설정 형식이 올바르지 않습니다.')
    folder = str(data.get('test_dir', '')).strip()
    if not folder:
        raise ValueError('테스트 폴더를 지정하세요.')
    test_dir = pathlib.Path(folder).expanduser().resolve()
    browser = data.get('browser')
    if browser not in ('chromium', 'firefox', 'webkit'):
        raise ValueError('지원하는 브라우저를 선택하세요.')
    for key in ('headless', 'obsidian_enabled'):
        if not isinstance(data.get(key), bool):
            raise ValueError('설정 값이 올바르지 않습니다.')
    obsidian = str(data.get('obsidian_dir', '')).strip()
    if data['obsidian_enabled'] and (not obsidian or not pathlib.Path(obsidian).expanduser().is_dir()):
        raise ValueError('Obsidian 내보내기 폴더가 존재해야 합니다.')
    if create_personal_dir and test_dir == DATA.resolve() / 'tests':
        test_dir.mkdir(parents=True, exist_ok=True)
    if not test_dir.is_dir():
        raise ValueError('존재하는 테스트 폴더를 지정하세요.')
    project_config = str(data.get('project_config', '')).strip()
    if project_config:
        config_path = pathlib.Path(project_config).expanduser().resolve()
        if not config_path.is_file() or config_path.suffix.lower() not in ('.js', '.cjs', '.mjs', '.ts', '.mts', '.cts'):
            raise ValueError('존재하는 Playwright 설정 파일을 지정하세요.')
        project_config = str(config_path)
    return {'test_dir': str(test_dir), 'browser': browser, 'headless': data['headless'],
            'obsidian_enabled': data['obsidian_enabled'],
            'obsidian_dir': str(pathlib.Path(obsidian).expanduser().resolve()) if obsidian else '',
            'project_config': project_config, 'setup_complete': True}


MAC_FOLDER_PICKER = '''
on run argv
    set startFolder to (POSIX file (item 1 of argv)) as alias
    try
        set chosenFolder to choose folder with prompt "테스트 폴더를 선택하세요." default location startFolder
        return POSIX path of chosenFolder
    on error number -128
        return ""
    end try
end run
'''

WINDOWS_FOLDER_PICKER = '''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$dialog = [System.Windows.Forms.FolderBrowserDialog]::new()
try {
    $dialog.Description = $env:BTW_PICKER_TITLE
    $dialog.SelectedPath = $env:BTW_PICKER_START
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::Write($dialog.SelectedPath)
    }
} finally {
    $dialog.Dispose()
}
'''


def choose_test_folder(initial_path):
    if not isinstance(initial_path, str):
        raise ValueError('시작 폴더 경로가 올바르지 않습니다.')
    start = pathlib.Path(initial_path.strip()).expanduser() if initial_path.strip() else pathlib.Path.home()
    if not start.is_dir():
        start = start.parent if start.parent.is_dir() else pathlib.Path.home()
    start = str(start.resolve())

    if sys.platform == 'darwin':
        command = ['/usr/bin/osascript', '-e', MAC_FOLDER_PICKER, start]
        options = {}
    elif sys.platform == 'win32':
        powershell = pathlib.Path(os.environ.get('SystemRoot', r'C:\\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        command = [str(powershell), '-NoProfile', '-STA', '-Command', WINDOWS_FOLDER_PICKER]
        options = {'creationflags': subprocess.CREATE_NO_WINDOW,
                   'env': {**os.environ, 'BTW_PICKER_START': start, 'BTW_PICKER_TITLE': '테스트 폴더를 선택하세요.'}}
    else:
        raise ValueError('이 환경에서는 폴더 경로를 직접 입력하세요.')

    if not FOLDER_PICKER_LOCK.acquire(blocking=False):
        raise ValueError('이미 열린 폴더 선택 창에서 선택하거나 취소하세요.')
    try:
        result = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace',
                                timeout=300, check=True, **options)
        return result.stdout.rstrip('\r\n') or None
    except subprocess.TimeoutExpired:
        raise ValueError('폴더 선택 시간이 지났습니다. 다시 시도하세요.') from None
    except (OSError, subprocess.CalledProcessError):
        raise ValueError('폴더 선택 창을 열지 못했습니다. 경로를 직접 입력할 수 있습니다.') from None
    finally:
        FOLDER_PICKER_LOCK.release()


def run_dir(rid):
    if not isinstance(rid, str) or len(rid) != 32 or any(c not in '0123456789abcdef' for c in rid):
        raise ValueError('실행 ID가 올바르지 않습니다.')
    folder = (RUNS / rid).resolve()
    if folder.parent != RUNS.resolve() or not folder.is_dir():
        raise ValueError('실행 기록을 찾을 수 없습니다.')
    return folder


def events(folder):
    result = []
    try:
        with (folder / 'events.jsonl').open(encoding='utf-8') as f:
            for line in f:
                try:
                    result.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return result


def artifacts(folder):
    output = folder / 'artifacts'
    if not output.is_dir():
        return []
    return [p.relative_to(folder).as_posix() for p in sorted(output.rglob('*'))
            if p.is_file() and p.resolve().is_relative_to(output.resolve())]


def git_checkout(directory):
    git = shutil.which('git')
    if not git:
        return None

    def read(*args):
        try:
            result = subprocess.run(
                [git, '-C', str(directory), '--no-optional-locks', '-c', 'core.fsmonitor=false', *args],
                capture_output=True, encoding='utf-8', errors='replace', timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    commit = read('rev-parse', '--verify', 'HEAD')
    if not commit:
        return None
    branch = read('symbolic-ref', '--quiet', '--short', 'HEAD')
    changes = read('status', '--porcelain', '--untracked-files=normal')
    return {'commit': commit, 'branch': branch, 'dirty': None if changes is None else bool(changes)}


def finish_run(rid, config):
    global ACTIVE, ACTIVE_PROCESS, STOP_REQUESTED
    folder = RUNS / rid
    meta = read_json(folder / 'run.json', {})
    env = os.environ.copy()
    env.update(BTW_TEST_DIR=config['test_dir'], BTW_BROWSER=config['browser'],
               BTW_HEADLESS=str(config['headless']).lower(), BTW_OUTPUT_DIR=str(folder / 'artifacts'),
               BTW_EVENTS=str(folder / 'events.jsonl'))
    env['NODE_PATH'] = os.pathsep.join(filter(None, [str(ROOT / 'node_modules'), env.get('NODE_PATH', '')]))
    cli = ROOT / 'node_modules' / '@playwright' / 'test' / 'cli.js'
    try:
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Node.js를 찾을 수 없습니다. 설치 후 다시 실행하세요.')
        if not cli.is_file():
            raise RuntimeError('Playwright가 설치되지 않았습니다. 프로젝트 폴더에서 npm install을 실행하세요.')
        project_config = config.get('project_config', '')
        if project_config:
            project_root = pathlib.Path(project_config).parent
            project_cli = project_root / 'node_modules' / '@playwright' / 'test' / 'cli.js'
            if project_cli.is_file():
                cli = project_cli
            command = [node, str(cli), 'test', '--config', project_config,
                       '--add-reporter', str(ROOT / 'app' / 'reporter.cjs'),
                       '--output', str(folder / 'artifacts')]
            cwd = project_root
        else:
            command = [node, str(cli), 'test', '--config', str(ROOT / 'app' / 'playwright.config.cjs')]
            cwd = ROOT
        git_dir = cwd if project_config else pathlib.Path(config['test_dir'])
        meta['context']['git'] = git_checkout(git_dir)
        with (folder / 'console.txt').open('w', encoding='utf-8') as output:
            with LOCK:
                if STOP_REQUESTED:
                    meta['status'] = 'interrupted'
                    return
                options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
                p = subprocess.Popen(command, cwd=str(cwd), env=env, stdout=output,
                                     stderr=subprocess.STDOUT, **options)
                ACTIVE_PROCESS = p
            p.wait()
        final = next((e for e in reversed(events(folder)) if e.get('type') == 'end'), {})
        with LOCK:
            stopped = STOP_REQUESTED
        meta['status'] = 'interrupted' if stopped else ('passed' if p.returncode == 0 and final.get('status') == 'passed' else 'failed')
        meta['exit_code'] = p.returncode
        if not final and not stopped:
            meta['error'] = '테스트 실행에 실패했습니다. 실행 로그를 확인하세요.'
    except Exception as e:
        meta['status'] = 'failed'
        meta['error'] = str(e)
    finally:
        meta['ended_at'] = time.time()
        with LOCK:
            write_json(folder / 'run.json', meta)
            ACTIVE = None
            ACTIVE_PROCESS = None
            STOP_REQUESTED = False


def stop_run(rid):
    global STOP_REQUESTED
    with LOCK:
        if not isinstance(rid, str) or rid != ACTIVE:
            raise ValueError('현재 실행 중인 테스트가 아닙니다.')
        if STOP_REQUESTED:
            return
        process = ACTIVE_PROCESS
        if process is not None:
            if process.poll() is not None:
                raise ValueError('이미 종료 중인 테스트입니다. 결과를 확인하세요.')
            try:
                if os.name == 'nt':
                    result = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                            capture_output=True, timeout=10, check=False,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode:
                        raise OSError('taskkill failed')
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except (OSError, subprocess.TimeoutExpired):
                raise ValueError('테스트 중단을 요청하지 못했습니다. 실행 로그를 확인하세요.') from None
        STOP_REQUESTED = True


def test_files(folder):
    root = pathlib.Path(folder)
    if not root.is_dir():
        return []
    matches = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in ('node_modules', '.git', 'test-results')]
        matches.extend(pathlib.Path(directory) / name for name in files
                       if re.search(r'\.(spec|test)\.(js|jsx|ts|tsx|mjs|cjs|mts|cts)$', name))
    return matches


def start_run(*, example=False):
    global ACTIVE, STOP_REQUESTED
    with LOCK:
        if ACTIVE:
            raise ValueError('이미 테스트가 실행 중입니다.')
        config = settings()
        if not config['setup_complete']:
            raise ValueError('먼저 초기 설정을 완료하세요.')
        if example:
            config = {**config, 'test_dir': str(ROOT / 'examples'), 'project_config': ''}
        config = validate_settings(config)
        if not config.get('project_config') and not test_files(config['test_dir']):
            raise ValueError('테스트 파일이 없습니다. 파일을 저장한 뒤 실행하세요.')
        rid = uuid.uuid4().hex
        folder = RUNS / rid
        folder.mkdir(parents=True)
        mode = 'example' if example else 'project' if config['project_config'] else 'folder'
        write_json(folder / 'run.json', {'id': rid, 'status': 'running', 'started_at': time.time(),
                                        'test_dir': config.get('project_config') or config['test_dir'],
                                        'browser': '프로젝트 설정' if config.get('project_config') else config['browser'],
                                        'context': {'mode': mode}})
        ACTIVE = rid
        STOP_REQUESTED = False
        threading.Thread(target=finish_run, args=(rid, config), daemon=True).start()
        return rid


def import_history(path):
    """기존 out/ 결과와 선택적인 이벤트 로그를 로컬 기록으로 복사한다."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError('기존 결과 폴더 경로를 입력하세요.')
    source = pathlib.Path(path.strip()).expanduser().resolve()
    if not source.is_dir():
        raise ValueError('기존 결과 폴더를 찾을 수 없습니다.')
    out = source / 'out' if (source / 'out').is_dir() else source
    folders = sorted(p for p in out.iterdir() if p.is_dir() and (p / 'result.json').is_file())
    log_path = out.parent / 'dashboard' / 'events.jsonl'
    log_by_run = {}
    if log_path.is_file():
        with log_path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                    if isinstance(entry, dict) and isinstance(entry.get('run'), str):
                        log_by_run.setdefault(entry['run'], []).append(entry)
                except ValueError:
                    continue
    known = {folder.name for folder in folders}
    for name, entries in log_by_run.items():
        candidate = out / name
        if (name not in known and name not in ('.', '..') and pathlib.Path(name).name == name
                and candidate.is_dir() and candidate.resolve().parent == out.resolve()
                and any(e.get('type') == 'run_start' for e in entries)):
            folders.append(candidate)
    if not folders:
        raise ValueError('가져올 실행 결과나 이벤트 기록을 찾을 수 없습니다.')
    imported, skipped, invalid = 0, 0, 0
    RUNS.mkdir(parents=True, exist_ok=True)
    with LOCK:
        for source_run in folders:
            rid = uuid.uuid5(uuid.NAMESPACE_URL, str(source_run.resolve())).hex
            dest = RUNS / rid
            if dest.exists():
                skipped += 1
                continue
            run_events = log_by_run.get(source_run.name, [])
            result_path = source_run / 'result.json'
            result = read_json(result_path, None) if result_path.is_file() else {}
            if not isinstance(result, dict):
                invalid += 1
                continue
            first = next((e for e in run_events if e.get('type') == 'run_start'), {})
            last = next((e for e in reversed(run_events) if e.get('type') == 'run_end'), {})
            if last.get('status') in ('failed', 'aborted', 'unknown'):
                status = {'failed': 'failed', 'aborted': 'interrupted', 'unknown': 'unknown'}[last['status']]
            elif not result and not last:
                status = 'unknown'
            else:
                status = 'failed' if result.get('error') else ('passed' if result or last.get('status') == 'passed' else 'unknown')
            def timestamp(value, default):
                if not value:
                    return default
                try:
                    return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
                except (TypeError, ValueError):
                    return default
            started = timestamp(result.get('startedAt'), first.get('t', source_run.stat().st_mtime * 1000) / 1000)
            ended = timestamp(result.get('finishedAt'), last.get('t', started * 1000) / 1000)
            name = result.get('name') or (first.get('meta') or {}).get('title') or source_run.name
            run_error = str(last.get('error') or result.get('error') or '')
            metadata = {'id': rid, 'status': status, 'started_at': started, 'ended_at': ended,
                        'test_dir': '가져온 기록 · ' + str(name), 'browser': '기존 실행',
                        'source_type': 'cli_history', 'error': run_error}
            converted = [{'type': 'begin', 'total': 1}]
            descriptions = (first.get('meta') or {}).get('steps') or {}
            for entry in run_events:
                if entry.get('type') not in ('step_end', 'step_fail'):
                    continue
                side = entry.get('side', '')
                index = entry.get('i')
                items = descriptions.get(side, [])
                desc = items[index].get('desc', '') if isinstance(index, int) and 0 <= index < len(items) else ''
                label = {'legacy': '레거시', 'renewal': '리뉴얼'}.get(side, side)
                text = desc or entry.get('k') or '단계'
                converted.append({'type': 'step_end', 'title': label + ' · ' + str(text),
                                  'duration': entry.get('ms'), 'error': str(entry.get('error') or '')})
            if len(converted) == 1:
                converted.extend({'type': 'step_end', 'title': str(item)} for item in result.get('log', []) if isinstance(item, str))
            converted.append({'type': 'test_end', 'id': rid, 'title': str(name), 'status': status, 'error': run_error})
            converted.append({'type': 'end', 'status': status})
            temp = RUNS / ('.import-' + uuid.uuid4().hex)
            try:
                (temp / 'artifacts').mkdir(parents=True)
                for file in source_run.iterdir():
                    if not file.is_file() or file.resolve().parent != source_run.resolve():
                        continue
                    if file.name == 'report.md' or file.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.xls', '.xlsx'):
                        shutil.copy2(file, temp / 'artifacts' / file.name)
                write_json(temp / 'run.json', metadata)
                (temp / 'events.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in converted), encoding='utf-8')
                temp.rename(dest)
                imported += 1
            except (OSError, ValueError):
                shutil.rmtree(temp, ignore_errors=True)
                invalid += 1
    return {'imported': imported, 'skipped': skipped, 'invalid': invalid}


def history():
    RUNS.mkdir(parents=True, exist_ok=True)
    result = []
    for folder in RUNS.iterdir():
        if folder.is_dir():
            meta = read_json(folder / 'run.json', None)
            if isinstance(meta, dict):
                if meta.get('status') == 'running' and folder.name != ACTIVE:
                    meta['status'] = 'interrupted'
                result.append(meta)
    return sorted(result, key=lambda x: x.get('started_at', 0), reverse=True)


def context_fields(meta, records):
    context = meta.get('context')
    if not context:
        return []
    mode = context['mode']
    mode_label = {'example': '예제', 'project': '프로젝트 설정', 'folder': '폴더 테스트'}[mode]
    fields = [
        ('실행 방식', mode_label),
        ('설정 파일' if mode == 'project' else '테스트 폴더', meta['test_dir']),
    ]
    git = context.get('git')
    if git:
        fields.extend([
            ('Git 커밋', git['commit']),
            ('Git 브랜치', git['branch'] or '분리된 HEAD'),
            ('작업 트리', {True: '변경 있음', False: '변경 없음', None: '확인 불가'}[git['dirty']]),
        ])
    for record in records:
        if record.get('type') != 'begin':
            continue
        for project in record.get('projects', []):
            details = ' · '.join(filter(None, (project['name'], project['browser'], project['origin'])))
            fields.append(('Playwright 설정', details))
        break
    return fields


def trace_paths(paths):
    return [path for path in paths if pathlib.PurePosixPath(path).name == 'trace.zip']


def verification_results(records):
    latest_attempts = {record['id']: record.get('retry', 0) for record in records
                       if record.get('type') == 'test_begin'}
    checks = [record for record in records if record.get('type') == 'check_end'
              and record['retry'] == latest_attempts.get(record['id'], 0)]
    passed = sum(check['status'] == 'passed' for check in checks)
    return {'passed': passed, 'failed': len(checks) - passed, 'checks': checks}


def report_text(value):
    return html.escape(' '.join(value.splitlines())).replace('[', '&#91;')


def make_report(folder):
    meta = read_json(folder / 'run.json', {})
    records = events(folder)
    paths = artifacts(folder)
    report = ['# 브라우저 테스트 결과', '', '- 실행 ID: ' + folder.name,
              '- 상태: ' + str(meta.get('status', '')), '']
    fields = context_fields(meta, records)
    if fields:
        report.extend(['## 실행 조건', ''])
        for label, value in fields:
            report.append(f'- {label}: {report_text(value)}')
        report.append('')
    verification = verification_results(records)
    if verification['checks']:
        report.extend(['## 검증 항목', '',
                       f"통과 {verification['passed']}건 · 실패 {verification['failed']}건", ''])
        for check in verification['checks']:
            status = '통과' if check['status'] == 'passed' else '실패'
            report.append(f"- **{status}** {report_text(check['title'])} ({report_text(check['test'])})")
        report.append('')
    for e in records:
        if e.get('type') == 'test_end':
            report.extend(['## ' + e.get('title', ''), '', '- 상태: ' + e.get('status', ''), '', e.get('error', ''), ''])
    for rel in paths:
        if pathlib.Path(rel).suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
            report.append('![](' + rel.replace(' ', '%20') + ')')
    traces = trace_paths(paths)
    if traces:
        report.extend(['', '## Playwright 트레이스', ''])
        for number, rel in enumerate(traces, 1):
            report.append(f'- [트레이스 {number} 다운로드]({quote(rel)})')
    return '\n'.join(report)


STATUS_LABELS = {'running': '실행 중', 'passed': '성공', 'failed': '실패',
                 'interrupted': '중단', 'unknown': '알 수 없음',
                 'skipped': '건너뜀', 'timedOut': '시간 초과'}


def make_handoff(folder):
    meta = read_json(folder / 'run.json', {})
    status = meta.get('status', 'unknown')
    if status == 'running':
        if folder.name == ACTIVE:
            raise ValueError('실행이 끝난 뒤 인수인계를 열 수 있습니다.')
        status = 'interrupted'

    records = events(folder)
    verification = verification_results(records)
    started_at = meta.get('started_at')
    when = time.strftime('%Y-%m-%d %H:%M:%S %z', time.localtime(started_at)) if started_at else ''
    result = ' · '.join(filter(None, (STATUS_LABELS.get(status, '알 수 없음'), when)))
    lines = ['# 브라우저 테스트 인수인계', '', '## 확인한 결과', '',
             f'- **실행 상태** {result}']
    if meta.get('test_dir'):
        lines.append(f"- **실행 대상** {report_text(meta['test_dir'])}")
    git = (meta.get('context') or {}).get('git')
    if git:
        branch = report_text(git['branch'] or '분리된 HEAD')
        dirty = {True: '변경 있음', False: '변경 없음', None: '확인 불가'}[git['dirty']]
        lines.append(f"- **Git** {branch} · `{git['commit']}` · 작업 트리 {dirty}")
    if verification['checks']:
        lines.append(f"- **검증 항목** 통과 {verification['passed']}건 · 실패 {verification['failed']}건")
    failed_checks = [check['title'] for check in verification['checks'] if check['status'] == 'failed']
    if failed_checks:
        first = ' · '.join(report_text(title) for title in failed_checks[:3])
        remaining = f' 외 {len(failed_checks) - 3}건' if len(failed_checks) > 3 else ''
        lines.append(f'- **실패한 조건** {first}{remaining}')
    elif status == 'failed':
        failure = next((record for record in records if record.get('type') in ('step_end', 'test_end')
                        and record.get('error') and record.get('title')), None)
        if failure:
            lines.append(f"- **실패 위치** {report_text(failure['title'])}")

    address = f'http://127.0.0.1:{PORT}'
    query = urlencode({'id': folder.name})
    next_step = {
        'passed': '다음 변경 후 같은 테스트를 실행해 이 보고서와 비교합니다.',
        'failed': '보고서의 실패 상세와 실행 로그를 확인해 재현할 조건을 정합니다.',
        'interrupted': '마지막 실행 단계를 확인하고 재실행 범위를 정합니다.',
    }.get(status, '보고서와 실행 로그에서 확인한 범위를 점검합니다.')
    lines.extend(['', '## 근거', '',
                  f'- [보고서]({address}/api/report?{query})',
                  f'- [실행 로그]({address}/api/log?{query})',
                  f'- **기록 폴더** {report_text(str(folder))}',
                  '', '## 다음 할 일', '', next_step, ''])
    return '\n'.join(lines)


def execution_log(folder, records):
    console = (folder / 'console.txt').resolve()
    if console.parent == folder.resolve() and console.is_file():
        output = console.read_text(encoding='utf-8', errors='replace')
        if output.strip():
            return '콘솔 출력', output

    event_labels = {'test_begin': '테스트 시작', 'test_end': '테스트 결과',
                    'step_end': '단계', 'check_end': '검증', 'error': '오류', 'end': '실행 결과'}
    lines = []
    for record in records:
        label = event_labels.get(record.get('type'))
        if not label:
            continue
        status = STATUS_LABELS.get(record.get('status'), record.get('status'))
        if record.get('type') == 'step_end' and record.get('error'):
            status = '실패'
        parts = [label, record.get('title'), status, record.get('error')]
        lines.append(' · '.join(str(part) for part in parts if part))
    return '저장된 실행 기록', '\n'.join(lines) or '저장된 실행 로그가 없습니다.'


def report_html(folder):
    meta = read_json(folder / 'run.json', {})
    evs = events(folder)
    status = meta.get('status', '')
    if status == 'running' and folder.name != ACTIVE:
        status = 'interrupted'
    esc = lambda value: html.escape(str(value or ''), quote=True)
    def status_label(value):
        return esc(STATUS_LABELS.get(value, value))
    started = meta.get('started_at')
    when = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started)) if started else '-'
    finished = meta.get('ended_at') or time.time()
    duration = str(max(0, round(finished - started))) + '초' if started else '-'
    tests = [e for e in evs if e.get('type') == 'test_end']
    steps = [e for e in evs if e.get('type') == 'step_end']
    test_rows = ''.join('<li><strong>' + esc(e.get('title')) + '</strong><span class="badge">' +
                        status_label(e.get('status')) + '</span>' +
                        ('<pre class="error">' + esc(e['error']) + '</pre>' if e.get('error') else '') + '</li>'
                        for e in tests) or '<li class="muted">완료된 테스트가 없습니다.</li>'
    step_rows = ''.join('<li><span class="mark">' + ('✗' if e.get('error') else '✓') + '</span> ' +
                        esc(e.get('title')) + ('<pre class="error">' + esc(e['error']) + '</pre>' if e.get('error') else '') + '</li>'
                        for e in steps) or '<li class="muted">기록된 단계가 없습니다.</li>'
    verification = verification_results(evs)
    verification_section = ''
    if verification['checks']:
        check_rows = []
        for check in verification['checks']:
            status = '통과' if check['status'] == 'passed' else '실패'
            details = ('<details><summary>실패 상세</summary><pre class="error">' +
                       esc(check['error']) + '</pre></details>') if check.get('error') else ''
            check_rows.append(f"""<li><strong>{esc(check['title'])}</strong><span class="badge">{status}</span>
<div class="meta">{esc(check['test'])}</div>{details}</li>""")
        verification_section = f"""<section class="card" id="verification-results"><h2>검증 항목</h2>
<p class="meta">통과 {verification['passed']}건 · 실패 {verification['failed']}건</p>
<ul>{''.join(check_rows)}</ul></section>"""
    paths = artifacts(folder)
    image_paths = [p for p in paths if pathlib.Path(p).suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp')]
    def image(p):
        url = '/api/file?' + urlencode({'id': folder.name, 'path': p})
        return '<figure><button type="button" class="preview-trigger" aria-label="' + esc(pathlib.PurePosixPath(p).name) + ' 확대"><img src="' + esc(url) + '" alt="' + esc(pathlib.PurePosixPath(p).name) + '"></button><figcaption>' + esc(pathlib.PurePosixPath(p).name) + '</figcaption></figure>'
    captures = ''.join(image(p) for p in image_paths) or '<p class="muted">저장된 캡처가 없습니다.</p>'
    fields = context_fields(meta, evs)
    context_rows = ''.join(f'<li><strong>{esc(label)}</strong> {esc(value)}</li>' for label, value in fields)
    context_section = f'<section class="card"><h2>실행 조건</h2><ul>{context_rows}</ul></section>' if fields else ''
    traces = []
    for number, rel in enumerate(trace_paths(paths), 1):
        url = esc('/api/file?' + urlencode({'id': folder.name, 'path': rel}))
        traces.append(f'<li><a href="{url}" download>트레이스 {number} 다운로드</a></li>')
    trace_section = ('<section class="card"><h2>Playwright 트레이스</h2><ul>' +
                     ''.join(traces) + '</ul></section>') if traces else ''
    error = '<pre class="error">' + esc(meta['error']) + '</pre>' if meta.get('error') else ''
    source_report = (folder / 'artifacts' / 'report.md').resolve()
    original = ''
    report_scripts = ''
    if (meta.get('source_type') == 'cli_history' and source_report.parent == folder.resolve() / 'artifacts'
            and source_report.is_file()):
        source = esc(source_report.read_text(encoding='utf-8', errors='replace'))
        original = ('<section class="card"><h2>기존 상세 보고서</h2>'
                    f'<div id="imported-report"><pre>{source}</pre></div></section>')
        report_scripts = '<script type="module" src="/imported-report.js"></script>'
    content = f"""
<div class="summary"><h1>테스트 결과 보고서</h1><p><span class="badge">{status_label(status)}</span> 테스트 {len(tests)}건</p><div class="meta"><span>시작 {esc(when)}</span><span>소요 {esc(duration)}</span><span>브라우저 {esc(meta.get('browser'))}</span><span>실행 ID {esc(folder.name)}</span></div>{error}</div>
{context_section}
{verification_section}
<section class="card"><h2>테스트 결과</h2><ul>{test_rows}</ul></section>
<section class="card"><h2>실행 단계</h2><ul>{step_rows}</ul></section>
{trace_section}
<section class="card"><h2>캡처 {len(image_paths)}장</h2><div class="gallery">{captures}</div></section>
{original}
"""
    return result_page('테스트 결과 보고서', content,
                       '<link rel="stylesheet" href="/preview.css">',
                       report_scripts + '<script src="/preview.js"></script>')


def log_html(folder):
    label, output = execution_log(folder, events(folder))
    run = read_json(folder / 'run.json', {})
    name = html.escape(str(run.get('test_dir') or folder.name))
    content = f"""
<section class="card" id="execution-log">
  <h1>실행 로그</h1>
  <p class="meta">{name}</p>
  <h2>{html.escape(label)}</h2>
  <pre>{html.escape(output)}</pre>
</section>
"""
    return result_page('실행 로그', content)


def result_page(title, content, head='', scripts=''):
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>{head}
<style>
:root{{color-scheme:light dark;--bg:#f5f7fb;--card:#fff;--ink:#1e293b;--muted:#607086;--line:#dbe2ec;--accent:#2459c5;--soft:#e7eeff;--bad:#b52c35}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111824;--card:#1a2433;--ink:#ebf0f7;--muted:#a4b3c6;--line:#344253;--accent:#9cbaff;--soft:#273957;--bad:#ff9292}}}}
*{{box-sizing:border-box}}body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,'Malgun Gothic',sans-serif;background:var(--bg);color:var(--ink);margin:0}}main{{max-width:1000px;margin:auto;padding:28px 20px}}h1{{font-size:24px;margin:0 0 8px}}h2{{font-size:17px;margin:0 0 12px}}.summary,.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:16px}}.meta{{display:flex;gap:10px 24px;flex-wrap:wrap;color:var(--muted);font-size:13px}}.badge{{display:inline-block;padding:2px 10px;background:var(--soft);border-radius:99px;color:var(--accent);font-weight:700;font-size:13px}}ul{{list-style:none;margin:0;padding:0}}li{{padding:10px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}}li:last-child{{border-bottom:0}}li .badge{{margin-left:10px}}.mark{{color:var(--accent)}}.muted,figcaption{{color:var(--muted)}}.gallery{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}}figure{{margin:0}}img{{max-width:100%;border:1px solid var(--line);border-radius:8px}}figcaption{{overflow-wrap:anywhere;font-size:13px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}.error{{color:var(--bad)}}
#imported-report{{overflow-x:auto;overflow-wrap:anywhere}} #imported-report table{{border-collapse:collapse}} #imported-report th,#imported-report td{{padding:6px 10px;border:1px solid var(--line);text-align:left}} #imported-report ul,#imported-report ol{{list-style:revert;padding-inline-start:24px}} #imported-report li{{padding:2px 0;border:0}}
</style>
</head><body><main>{content}</main>{scripts}</body></html>"""


def export_run(folder):
    cfg = settings()
    if not cfg['obsidian_enabled']:
        raise ValueError('설정에서 Obsidian 내보내기를 먼저 켜세요.')
    dest = pathlib.Path(cfg['obsidian_dir']) / ('browser-test-' + folder.name)
    if dest.exists():
        raise ValueError('같은 실행을 이미 내보냈습니다.')
    dest.mkdir()
    for rel in artifacts(folder):
        source = folder / rel
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (dest / 'report.md').write_text(make_report(folder), encoding='utf-8')
    return str(dest)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, data, content_type='application/json; charset=utf-8'):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        elif isinstance(data, str):
            data = data.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get('Host') != '127.0.0.1:' + str(PORT):
            return self.send(403, {'error': '허용되지 않은 호스트입니다.'})
        u = urlparse(self.path)
        if u.path == '/':
            page = (ROOT / 'app' / 'index.html').read_text(encoding='utf-8').replace('__TOKEN__', TOKEN)
            return self.send(200, page, 'text/html; charset=utf-8')
        if u.path in ('/preview.css', '/preview.js', '/imported-report.js'):
            name = u.path.lstrip('/')
            content_type = 'text/css; charset=utf-8' if name.endswith('.css') else 'text/javascript; charset=utf-8'
            return self.send(200, (ROOT / 'app' / name).read_bytes(), content_type)
        if u.path == '/markdown-it.mjs':
            script = ROOT / 'node_modules/markdown-it/dist/browser/markdown-it.esm.min.mjs'
            if script.is_file():
                return self.send(200, script.read_bytes(), 'text/javascript; charset=utf-8')
        if u.path == '/api/state':
            return self.send(200, {'settings': settings(), 'runs': history(), 'active': ACTIVE, 'stopping': STOP_REQUESTED,
                                   'folders': {'examples': str(ROOT / 'examples'), 'personal': str(DATA.resolve() / 'tests')},
                                   'test_file_count': len(test_files(settings()['test_dir'])),
                                   'node': bool(shutil.which('node')), 'playwright': (ROOT / 'node_modules/@playwright/test/cli.js').is_file()})
        if u.path == '/api/run':
            try:
                folder = run_dir(parse_qs(u.query).get('id', [''])[0])
                run = read_json(folder / 'run.json', {})
                if run.get('status') == 'running' and folder.name != ACTIVE:
                    run['status'] = 'interrupted'
                records = events(folder)
                return self.send(200, {'run': run, 'events': [e for e in records if e.get('type') != 'check_end'],
                                       'verification': verification_results(records), 'artifacts': artifacts(folder)})
            except ValueError as e:
                return self.send(404, {'error': str(e)})
        if u.path in ('/api/report', '/api/log'):
            try:
                folder = run_dir(parse_qs(u.query).get('id', [''])[0])
                render = report_html if u.path == '/api/report' else log_html
                return self.send(200, render(folder), 'text/html; charset=utf-8')
            except ValueError as e:
                return self.send(404, {'error': str(e)})
        if u.path == '/api/handoff':
            try:
                folder = run_dir(parse_qs(u.query).get('id', [''])[0])
            except ValueError as e:
                return self.send(404, {'error': str(e)})
            try:
                return self.send(200, {'markdown': make_handoff(folder)})
            except ValueError as e:
                return self.send(409, {'error': str(e)})
        if u.path == '/api/file':
            try:
                q = parse_qs(u.query)
                folder = run_dir(q.get('id', [''])[0])
                rel = q.get('path', [''])[0]
                if rel == 'console.txt':
                    _, log_text = execution_log(folder, events(folder))
                    return self.send(200, log_text, 'text/plain; charset=utf-8')
                p = (folder / rel).resolve()
                if not p.is_relative_to(folder) or not p.is_file() or not rel.startswith('artifacts/'):
                    raise ValueError('파일을 찾을 수 없습니다.')
                if rel == 'artifacts/report.md' and read_json(folder / 'run.json', {}).get('source_type') == 'cli_history':
                    return self.send(200, report_html(folder), 'text/html; charset=utf-8')
                mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.txt': 'text/plain; charset=utf-8', '.md': 'text/plain; charset=utf-8', '.zip': 'application/zip'}.get(p.suffix.lower(), 'application/octet-stream')
                return self.send(200, p.read_bytes(), mime)
            except (ValueError, OSError):
                return self.send(404, {'error': '파일을 찾을 수 없습니다.'})
        return self.send(404, {'error': '찾을 수 없습니다.'})

    def do_POST(self):
        origin = self.headers.get('Origin', '')
        if self.headers.get('Host') != '127.0.0.1:' + str(PORT) or self.headers.get('X-Workbench-Token') != TOKEN or (origin and origin != 'http://127.0.0.1:' + str(PORT)):
            return self.send(403, {'error': '허용되지 않은 요청입니다.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length > 65536:
                raise ValueError('요청이 너무 큽니다.')
            data = json.loads(self.rfile.read(length) or b'{}')
            if self.path == '/api/settings':
                with LOCK:
                    value = validate_settings(data, create_personal_dir=True)
                    write_json(SETTINGS, value)
                return self.send(200, {'ok': True})
            if self.path == '/api/pick-test-folder':
                if not isinstance(data, dict):
                    raise ValueError('폴더 선택 요청의 형식이 올바르지 않습니다.')
                return self.send(200, {'path': choose_test_folder(data.get('path', ''))})
            if self.path == '/api/import':
                return self.send(200, import_history(data.get('path')))
            if self.path == '/api/start':
                return self.send(200, {'id': start_run(example=data.get('example') is True)})
            if self.path == '/api/stop':
                stop_run(data.get('id'))
                return self.send(200, {'ok': True})
            if self.path == '/api/export':
                return self.send(200, {'path': export_run(run_dir(data.get('id')))})
            return self.send(404, {'error': '찾을 수 없습니다.'})
        except (ValueError, OSError, json.JSONDecodeError) as e:
            return self.send(400, {'error': str(e)})


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    server.daemon_threads = True
    address = 'http://127.0.0.1:' + str(PORT)
    print('브라우저 테스트 작업대:', address, flush=True)
    if os.environ.get('BTW_NO_OPEN') != '1':
        threading.Timer(0.5, lambda: webbrowser.open(address)).start()
    server.serve_forever()


if __name__ == '__main__':
    main()
