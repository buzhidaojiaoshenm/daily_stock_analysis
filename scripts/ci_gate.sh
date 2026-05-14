#!/usr/bin/env bash

set -euo pipefail

print_dev_dependency_hint() {
  {
    echo "Install backend development dependencies with:"
    echo "  python -m pip install -r requirements.txt"
    echo "  python -m pip install flake8 pytest"
    echo
    echo "Or run the local bootstrap helper:"
    echo "  ./scripts/dev_bootstrap.sh --backend-only"
  } >&2
}

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "ERROR: Missing required development tool: $command_name" >&2
    print_dev_dependency_hint
    exit 127
  fi
}

require_python_module() {
  local module_name="$1"
  if ! python -c "import ${module_name}" >/dev/null 2>&1; then
    echo "ERROR: Missing required Python module: $module_name" >&2
    print_dev_dependency_hint
    exit 127
  fi
}

syntax_check() {
  echo "==> backend-gate: Python syntax check"
  python -m py_compile main.py src/config.py src/auth.py src/analyzer.py src/notification.py
  python -m py_compile src/storage.py src/scheduler.py src/search_service.py
  python -m py_compile src/market_analyzer.py src/stock_analyzer.py
  python -m py_compile data_provider/*.py
}

flake8_checks() {
  echo "==> backend-gate: flake8 critical checks"
  require_command flake8
  flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
}

deterministic_checks() {
  echo "==> backend-gate: local deterministic checks"
  ./test.sh code
  ./test.sh yfinance
}

api_contract_check() {
  echo "==> backend-gate: API contract drift check"
  python scripts/api_contract.py --check
}

offline_test_suite() {
  echo "==> backend-gate: offline test suite"
  require_python_module pytest
  python -m pytest -m "not network"
}

run_all() {
  syntax_check
  api_contract_check
  flake8_checks
  deterministic_checks
  offline_test_suite
  echo "==> backend-gate: all checks passed"
}

phase="${1:-all}"

case "$phase" in
  all)
    run_all
    ;;
  syntax)
    syntax_check
    ;;
  api-contract)
    api_contract_check
    ;;
  flake8)
    flake8_checks
    ;;
  deterministic)
    deterministic_checks
    ;;
  offline-tests)
    offline_test_suite
    ;;
  *)
    echo "Usage: $0 [all|syntax|api-contract|flake8|deterministic|offline-tests]" >&2
    exit 2
    ;;
esac
