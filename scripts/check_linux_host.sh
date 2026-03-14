#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STRICT=0
REQUIRE_NODE=0
REQUIRE_DOCKER=0
REQUIRE_PLAYWRIGHT=0
REQUIRE_UBUNTU=0
SANDBOX_MODE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict)
      STRICT=1
      ;;
    --require-node)
      REQUIRE_NODE=1
      ;;
    --require-docker)
      REQUIRE_DOCKER=1
      ;;
    --require-playwright)
      REQUIRE_PLAYWRIGHT=1
      ;;
    --ubuntu)
      REQUIRE_UBUNTU=1
      ;;
    --sandbox)
      SANDBOX_MODE=1
      REQUIRE_DOCKER=1
      ;;
    -h|--help)
      cat <<'EOF'
Usage: bash scripts/check_linux_host.sh [options]

Options:
  --strict               Exit non-zero when optional checks are missing
  --require-node         Treat Node.js/npm as required
  --require-docker       Treat Docker Engine as required
  --require-playwright   Treat Playwright as required in the project venv
  --ubuntu               Require the host OS to be Ubuntu
  --sandbox              Require Docker because the target runtime mode is sandbox
  -h, --help             Show this help
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

FAILURES=0
WARNINGS=0
PYTHON_BIN=""
PROJECT_VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
PLAYWRIGHT_PYTHON=""
PLAYWRIGHT_VERSION=""
NODE_REQUIRED=$REQUIRE_NODE
OS_ID=""
OS_VERSION_ID=""

print_ok() {
  printf '[OK] %s\n' "$1"
}

print_warn() {
  WARNINGS=$((WARNINGS + 1))
  printf '[WARN] %s\n' "$1"
}

print_fail() {
  FAILURES=$((FAILURES + 1))
  printf '[FAIL] %s\n' "$1"
}

version_ge() {
  local version_python=""
  if command -v python3 >/dev/null 2>&1; then
    version_python="python3"
  elif command -v python >/dev/null 2>&1; then
    version_python="python"
  else
    return 1
  fi
  "$version_python" - "$1" "$2" <<'PY'
from sys import argv

def parse(value: str):
    parts = []
    for item in value.split('.'):
        digits = ''.join(ch for ch in item if ch.isdigit())
        parts.append(int(digits or 0))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])

current = parse(argv[1])
minimum = parse(argv[2])
raise SystemExit(0 if current >= minimum else 1)
PY
}

check_command() {
  local cmd="$1"
  local label="$2"
  if command -v "$cmd" >/dev/null 2>&1; then
    print_ok "$label found at $(command -v "$cmd")"
    return 0
  fi
  return 1
}

if [[ "$(uname -s)" == "Linux" ]]; then
  print_ok "Linux host detected"
else
  print_fail "This script is intended for Linux hosts only"
fi

if [[ -f /etc/os-release ]]; then
  OS_ID="$(. /etc/os-release && printf '%s' "${ID:-}")"
  OS_VERSION_ID="$(. /etc/os-release && printf '%s' "${VERSION_ID:-}")"
  if [[ -n "$OS_ID" ]]; then
    if [[ -n "$OS_VERSION_ID" ]]; then
      print_ok "Distribution detected: $OS_ID $OS_VERSION_ID"
    else
      print_ok "Distribution detected: $OS_ID"
    fi
  fi
else
  print_warn "/etc/os-release not found; distribution detection is unavailable"
fi

if [[ $REQUIRE_UBUNTU -eq 1 ]]; then
  if [[ "$OS_ID" == "ubuntu" ]]; then
    print_ok "Ubuntu host confirmed"
  else
    print_fail "Ubuntu is required for this installation flow"
  fi
fi

if [[ -f "$ROOT_DIR/pyproject.toml" ]]; then
  print_ok "pyproject.toml found"
else
  print_fail "pyproject.toml not found under $ROOT_DIR"
fi

if [[ -f "$ROOT_DIR/uv.lock" ]]; then
  print_ok "uv.lock found"
else
  print_warn "uv.lock not found; setup can still proceed but may resolve fresh dependency versions"
fi

if [[ -f "$ROOT_DIR/bridge/package.json" ]]; then
  NODE_REQUIRED=1
  print_ok "WhatsApp bridge package detected"
else
  print_warn "bridge/package.json not found; Node.js bridge checks will be skipped"
fi

if check_command python3 "python3"; then
  PYTHON_BIN="$(command -v python3)"
  PYTHON_VERSION="$($PYTHON_BIN -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  if version_ge "$PYTHON_VERSION" "3.11.0"; then
    print_ok "Python version $PYTHON_VERSION satisfies >= 3.11"
  else
    print_fail "Python version $PYTHON_VERSION is too old; require >= 3.11"
  fi
elif check_command python "python"; then
  PYTHON_BIN="$(command -v python)"
  PYTHON_VERSION="$($PYTHON_BIN -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  if version_ge "$PYTHON_VERSION" "3.11.0"; then
    print_ok "Python version $PYTHON_VERSION satisfies >= 3.11"
  else
    print_fail "Python version $PYTHON_VERSION is too old; require >= 3.11"
  fi
else
  print_fail "Python 3.11+ is required"
fi

if check_command git "git"; then
  GIT_VERSION="$(git --version | awk '{print $3}')"
  print_ok "git version $GIT_VERSION"
else
  print_fail "git is required to clone/update the repository"
fi

if check_command uv "uv"; then
  UV_VERSION="$(uv --version | awk '{print $2}')"
  print_ok "uv version $UV_VERSION"
else
  print_fail "uv is required to create the project environment"
fi

if [[ $NODE_REQUIRED -eq 1 ]]; then
  if check_command node "node"; then
    NODE_VERSION_RAW="$(node --version)"
    NODE_VERSION="${NODE_VERSION_RAW#v}"
    if version_ge "$NODE_VERSION" "20.0.0"; then
      print_ok "Node.js version $NODE_VERSION satisfies >= 20"
    else
      print_fail "Node.js version $NODE_VERSION is too old; require >= 20 for the WhatsApp bridge"
    fi
  else
    if [[ $REQUIRE_NODE -eq 1 ]]; then
      print_fail "Node.js >= 20 is required"
    else
      print_warn "Node.js >= 20 is recommended because bridge/package.json exists"
    fi
  fi

  if check_command npm "npm"; then
    NPM_VERSION="$(npm --version)"
    print_ok "npm version $NPM_VERSION"
  else
    if [[ $REQUIRE_NODE -eq 1 ]]; then
      print_fail "npm is required when Node.js support is requested"
    else
      print_warn "npm is recommended because bridge/package.json exists"
    fi
  fi
fi

if check_command docker "docker"; then
  DOCKER_VERSION="$(docker --version | awk '{print $3}' | tr -d ',')"
  print_ok "Docker Engine CLI version $DOCKER_VERSION"
  if docker info >/dev/null 2>&1; then
    print_ok "Docker daemon is reachable"
  else
    if [[ $REQUIRE_DOCKER -eq 1 ]]; then
      print_fail "Docker CLI exists but daemon is not reachable"
    else
      print_warn "Docker CLI exists but daemon is not reachable"
    fi
  fi
else
  if [[ $REQUIRE_DOCKER -eq 1 ]]; then
    print_fail "Docker Engine is required"
  else
    print_warn "Docker Engine not found; host mode still works, sandbox mode will not"
  fi
fi

if command -v docker >/dev/null 2>&1; then
  if id -nG 2>/dev/null | tr ' ' '\n' | grep -Fxq docker; then
    print_ok "Current user is in the docker group"
  else
    if [[ $REQUIRE_DOCKER -eq 1 ]]; then
      print_warn "Current user is not in the docker group; Docker may require sudo or a re-login after group changes"
    else
      print_warn "Current user is not in the docker group"
    fi
  fi

  if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet docker; then
      print_ok "docker.service is active"
    else
      if [[ $REQUIRE_DOCKER -eq 1 ]]; then
        print_warn "docker.service is not active"
      else
        print_warn "docker.service is not active"
      fi
    fi
  fi
fi

if [[ $SANDBOX_MODE -eq 1 && $REQUIRE_DOCKER -eq 1 && $FAILURES -eq 0 ]]; then
  print_ok "Sandbox runtime prerequisites satisfied"
fi

if command -v apt-get >/dev/null 2>&1; then
  print_ok "Detected package manager: apt"
elif command -v dnf >/dev/null 2>&1; then
  print_ok "Detected package manager: dnf"
elif command -v yum >/dev/null 2>&1; then
  print_ok "Detected package manager: yum"
elif command -v pacman >/dev/null 2>&1; then
  print_ok "Detected package manager: pacman"
elif command -v zypper >/dev/null 2>&1; then
  print_ok "Detected package manager: zypper"
else
  print_warn "No supported package manager detected automatically"
fi

if [[ -x "$PROJECT_VENV_PYTHON" ]]; then
  print_ok "Project venv detected at $ROOT_DIR/.venv"
  if "$PROJECT_VENV_PYTHON" -c 'import playwright' >/dev/null 2>&1; then
    PLAYWRIGHT_PYTHON="$PROJECT_VENV_PYTHON"
  fi
elif [[ -n "$PYTHON_BIN" ]] && "$PYTHON_BIN" -c 'import playwright' >/dev/null 2>&1; then
  PLAYWRIGHT_PYTHON="$PYTHON_BIN"
fi

if [[ -n "$PLAYWRIGHT_PYTHON" ]]; then
  PLAYWRIGHT_VERSION="$($PLAYWRIGHT_PYTHON -m playwright --version 2>/dev/null | awk '{print $2}')"
  if [[ -n "$PLAYWRIGHT_VERSION" ]]; then
    print_ok "Playwright Python package available (version $PLAYWRIGHT_VERSION)"
  else
    print_ok "Playwright Python package available"
  fi
else
  if [[ $REQUIRE_PLAYWRIGHT -eq 1 ]]; then
    print_fail "Playwright is not installed in the project environment"
  else
    print_warn "Playwright is not installed in the project environment; install it only if you need browser automation"
  fi
fi

if [[ -d "$ROOT_DIR/.git" ]]; then
  print_ok "Git repository metadata present"
else
  print_warn "Repository does not appear to be a git clone"
fi

echo
printf 'Summary: %s failure(s), %s warning(s)\n' "$FAILURES" "$WARNINGS"

if [[ $FAILURES -gt 0 ]]; then
  exit 1
fi

if [[ $STRICT -eq 1 && $WARNINGS -gt 0 ]]; then
  exit 2
fi

exit 0
