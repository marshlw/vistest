#!/usr/bin/env bash
# VisTest — запуск на Linux/macOS.
#   ./run.sh           поднять UI
#   ./run.sh test      прогнать тесты
#   ./run.sh doctor    диагностика
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for c in python3.12 python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo "Python 3.10+ не найден" >&2
  exit 1
fi

exec "$PY" run.py "$@"
