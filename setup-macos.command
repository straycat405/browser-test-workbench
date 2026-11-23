#!/bin/sh
cd "$(dirname "$0")" || exit 1
command -v python3 >/dev/null 2>&1 || { echo 'Python 3.9 이상을 먼저 설치하세요.'; read -r _; exit 1; }
command -v node >/dev/null 2>&1 || { echo 'Node.js 20 이상을 먼저 설치하세요.'; read -r _; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { echo 'Python 3.9 이상이 필요합니다.'; read -r _; exit 1; }
node -e 'process.exit(Number(process.versions.node.split(".")[0]) < 20 ? 1 : 0)' || { echo 'Node.js 20 이상이 필요합니다.'; read -r _; exit 1; }
command -v npm >/dev/null 2>&1 || { echo 'npm을 찾을 수 없습니다. Node.js 설치를 확인하세요.'; read -r _; exit 1; }
npm ci && npx playwright install chromium || { echo '설치에 실패했습니다. 위 오류를 확인하세요.'; read -r _; exit 1; }
echo '설치 완료. 시작 파일을 실행합니다.'
exec python3 app/server.py
