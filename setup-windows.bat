@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1 || (echo Python 3.9 이상을 먼저 설치하세요. & pause & exit /b 1)
where node >nul 2>&1 || (echo Node.js 20 이상을 먼저 설치하세요. & pause & exit /b 1)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 9))"
if errorlevel 1 (echo Python 3.9 이상이 필요합니다. & pause & exit /b 1)
node -e "process.exit(Number(process.versions.node.split('.')[0]) < 20 ? 1 : 0)"
if errorlevel 1 (echo Node.js 20 이상이 필요합니다. & pause & exit /b 1)
where npm >nul 2>&1 || (echo npm을 찾을 수 없습니다. Node.js 설치를 확인하세요. & pause & exit /b 1)
call npm ci
if errorlevel 1 (echo 패키지 설치 실패 & pause & exit /b 1)
call npx playwright install chromium
if errorlevel 1 (echo 브라우저 설치 실패 & pause & exit /b 1)
py -3 app\server.py
if errorlevel 1 pause
