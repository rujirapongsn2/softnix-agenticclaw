#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="python3"
WITH_PLAYWRIGHT=0
WITH_PLAYWRIGHT_DEPS=0
WITH_WHATSAPP=0
REQUIRE_DOCKER=0
REQUIRE_UBUNTU=0
SANDBOX_MODE=0
SKIP_CHECK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)
      shift
      if [[ $# -eq 0 ]]; then
        echo "--python requires a value" >&2
        exit 2
      fi
      PYTHON_BIN="$1"
      ;;
    --with-playwright)
      WITH_PLAYWRIGHT=1
      ;;
    --with-playwright-deps)
      WITH_PLAYWRIGHT=1
      WITH_PLAYWRIGHT_DEPS=1
      ;;
    --with-whatsapp)
      WITH_WHATSAPP=1
      ;;
    --require-docker)
      REQUIRE_DOCKER=1
      ;;
    --ubuntu)
      REQUIRE_UBUNTU=1
      ;;
    --sandbox)
      SANDBOX_MODE=1
      REQUIRE_DOCKER=1
      ;;
    --skip-check)
      SKIP_CHECK=1
      ;;
    -h|--help)
      cat <<'EOF'
Usage: bash scripts/setup_linux_host.sh [options]

Options:
  --python <bin>            Python binary to use for the venv (default: python3)
  --with-playwright         Install the Playwright Python package into the project venv
  --with-playwright-deps    Also run playwright browser install with system dependencies
  --with-whatsapp           Build the WhatsApp bridge with npm install + npm run build
  --require-docker          Fail preflight when Docker Engine is not available
  --ubuntu                  Require Ubuntu during preflight
  --sandbox                 Require Docker and validate sandbox-oriented host setup
  --skip-check              Skip scripts/check_linux_host.sh before setup
  -h, --help                Show this help
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done

run_check() {
  local args=()
  if [[ $WITH_WHATSAPP -eq 1 ]]; then
    args+=(--require-node)
  fi
  if [[ $REQUIRE_UBUNTU -eq 1 ]]; then
    args+=(--ubuntu)
  fi
  if [[ $REQUIRE_DOCKER -eq 1 ]]; then
    args+=(--require-docker)
  fi
  if [[ $SANDBOX_MODE -eq 1 ]]; then
    args+=(--sandbox)
  fi
  bash "$ROOT_DIR/scripts/check_linux_host.sh" "${args[@]}"
}

if [[ $SKIP_CHECK -eq 0 ]]; then
  run_check
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python binary not found: $PYTHON_BIN" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required but was not found on PATH" >&2
  exit 1
fi

cd "$ROOT_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  uv venv --python "$PYTHON_BIN" "$VENV_DIR"
fi

UV_PYTHON="$VENV_DIR/bin/python"
UV_PIP=(uv pip --python "$UV_PYTHON")

"${UV_PIP[@]}" install -e .

if [[ $WITH_PLAYWRIGHT -eq 1 ]]; then
  "${UV_PIP[@]}" install playwright
  if [[ $WITH_PLAYWRIGHT_DEPS -eq 1 ]]; then
    "$UV_PYTHON" -m playwright install --with-deps chromium
  else
    "$UV_PYTHON" -m playwright install chromium
  fi
fi

if [[ $WITH_WHATSAPP -eq 1 ]]; then
  if ! command -v node >/dev/null 2>&1; then
    echo "Node.js is required for --with-whatsapp" >&2
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required for --with-whatsapp" >&2
    exit 1
  fi
  (
    cd "$ROOT_DIR/bridge"
    npm install
    npm run build
  )
fi

mkdir -p "$HOME/.nanobot" "$HOME/.softnix/admin" "$HOME/.softnix/instances"

cat <<EOF

Setup completed.

Key paths:
  Repo: $ROOT_DIR
  Venv: $VENV_DIR
  nanobot: $VENV_DIR/bin/nanobot

Suggested next commands:
  $VENV_DIR/bin/nanobot status
  $VENV_DIR/bin/nanobot onboard
  $VENV_DIR/bin/nanobot softnix-admin

To bootstrap your first Softnix instance:
  $VENV_DIR/bin/nanobot softnix-init --instance-id default-prod --name "Default Production" --owner default --env prod --repo-root "$ROOT_DIR" --nanobot-bin "$VENV_DIR/bin/nanobot"

For Ubuntu sandbox mode:
  bash "$ROOT_DIR/scripts/check_linux_host.sh" --ubuntu --sandbox
EOF
