#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")" && pwd)"

if [[ ! -x "$project_dir/backend/.venv/bin/python" ]]; then
  echo "缺少后端虚拟环境，请先按照 README 安装依赖。" >&2
  exit 1
fi

if [[ ! -f "$project_dir/frontend/dist/index.html" ]]; then
  echo "缺少前端构建产物，请先在 frontend 目录运行 npm install && npm run build。" >&2
  exit 1
fi

cd "$project_dir/backend"
exec .venv/bin/python -m app

