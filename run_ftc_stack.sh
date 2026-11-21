#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
case "$OSTYPE" in
    msys*|cygwin*) mcp_python=./mcp_server/.venv/Scripts/python.exe ;;
    *) mcp_python=./mcp_server/.venv/bin/python ;;
esac

if [[ -n ${FTC_ADVANCED_PYTHON:-} ]]; then
    exec "$FTC_ADVANCED_PYTHON" -u run_ftc_stack.py "$@"
elif [[ -x $mcp_python ]]; then
    exec "$mcp_python" -u run_ftc_stack.py "$@"
elif command -v python3 >/dev/null; then
    exec python3 -u run_ftc_stack.py "$@"
elif command -v python >/dev/null; then
    exec python -u run_ftc_stack.py "$@"
fi

printf 'Install Python 3.11+ or set FTC_ADVANCED_PYTHON.\n' >&2
exit 1
