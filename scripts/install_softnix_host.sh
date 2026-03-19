#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
DEFAULT_SOFTNIX_HOME="$HOME/.softnix"
DEFAULT_CONFIG_PATH="$HOME/.nanobot/config.json"
DEFAULT_INSTANCE_ID="default-prod"
DEFAULT_INSTANCE_NAME="Default Production"
DEFAULT_OWNER="default"
DEFAULT_ENV="prod"
DEFAULT_ADMIN_HOST="127.0.0.1"
DEFAULT_ADMIN_PORT="18880"
DEFAULT_SANDBOX_PROFILE="balanced"
DEFAULT_SANDBOX_IMAGE="softnixclaw:latest"

INSTANCE_ID="$DEFAULT_INSTANCE_ID"
INSTANCE_NAME="$DEFAULT_INSTANCE_NAME"
OWNER="$DEFAULT_OWNER"
INSTANCE_ENV="$DEFAULT_ENV"
ADMIN_HOST="$DEFAULT_ADMIN_HOST"
ADMIN_PORT="$DEFAULT_ADMIN_PORT"
SOFTNIX_HOME="$DEFAULT_SOFTNIX_HOME"
PYTHON_BIN=""
SOURCE_CONFIG=""
SANDBOX_PROFILE="$DEFAULT_SANDBOX_PROFILE"
SANDBOX_IMAGE="$DEFAULT_SANDBOX_IMAGE"
GATEWAY_PORT=""
WITH_WHATSAPP=0
WITH_PLAYWRIGHT=0
WITH_PLAYWRIGHT_DEPS=0
AUTO_YES=0
NO_BROWSER=0
FORCE_INSTANCE=0
REBUILD_IMAGE=0
SKIP_INSTANCE_START=0
SKIP_ADMIN_START=0
PROVIDER_NAME=""
PROVIDER_MODEL=""
PROVIDER_API_KEY=""
PROVIDER_API_BASE=""
log() {
  printf '%s\n' "$*"
}

step() {
  printf '\n==> %s\n' "$*"
}

ok() {
  printf '[OK] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

fail() {
  printf '[FAIL] %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: bash scripts/install_softnix_host.sh [options]

End-to-end installer for Ubuntu hosts running Softnix Admin from source.

Options:
  --instance-id <id>            Softnix instance id (default: default-prod)
  --name <name>                 Softnix instance display name
  --owner <owner>               Owner/tenant label (default: default)
  --env <env>                   Environment label (default: prod)
  --softnix-home <path>         Override Softnix base directory (default: ~/.softnix)
  --admin-host <host>           Admin bind host (default: 127.0.0.1)
  --admin-port <port>           Admin port (default: 18880)
  --gateway-port <port>         Explicit gateway port for the instance
  --python <bin>                Python binary to use/create (default: auto-detect)
  --source-config <path>        Config template to copy into the instance
  --sandbox-profile <name>      strict | balanced | fast (default: balanced)
  --sandbox-image <image>       Sandbox image tag (default: softnixclaw:latest)
  --with-whatsapp               Install/build the WhatsApp bridge
  --with-playwright             Install Playwright browser support
  --with-playwright-deps        Install Playwright with system dependencies
  --provider <name>             Provider key to write into instance config
  --model <name>                Default model to write into instance config
  --api-key <value>             Provider API key to write into instance config
  --api-base <url>              Provider API base to write into instance config
  --force-instance              Overwrite an existing instance with the same id
  --rebuild-image               Always rebuild the sandbox image
  --skip-admin-start            Install and bootstrap only; do not start Softnix Admin
  --skip-instance-start         Install and bootstrap only; do not start the instance
  --no-browser                  Do not open the browser at the end
  -y, --yes                     Auto-confirm all install/build actions
  -h, --help                    Show this help
EOF
}

confirm() {
  local prompt="$1"
  if [[ $AUTO_YES -eq 1 ]]; then
    ok "$prompt -> yes"
    return 0
  fi

  local reply=""
  read -r -p "$prompt [y/N] " reply
  case "$reply" in
    y|Y|yes|YES)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

run_sudo() {
  sudo "$@"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

version_ge() {
  local current="$1"
  local minimum="$2"
  python3 - "$current" "$minimum" <<'PY'
from sys import argv

def parse(value: str):
    parts = []
    for item in value.split("."):
        digits = "".join(ch for ch in item if ch.isdigit())
        parts.append(int(digits or 0))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])

raise SystemExit(0 if parse(argv[1]) >= parse(argv[2]) else 1)
PY
}

require_ubuntu() {
  [[ -f /etc/os-release ]] || fail "This installer requires /etc/os-release"
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || fail "This installer currently supports Ubuntu only"
  ok "Ubuntu detected: ${PRETTY_NAME:-$ID}"
}

python_version_of() {
  local bin="$1"
  "$bin" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'
}

select_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    command_exists "$PYTHON_BIN" || fail "Python binary not found: $PYTHON_BIN"
    local version
    version="$(python_version_of "$PYTHON_BIN")"
    version_ge "$version" "3.11.0" || fail "Python binary $PYTHON_BIN is too old: $version"
    ok "Using Python binary: $PYTHON_BIN ($version)"
    return 0
  fi

  local candidates=(python3.12 python3.11 python3)
  local candidate=""
  for candidate in "${candidates[@]}"; do
    if command_exists "$candidate"; then
      local version
      version="$(python_version_of "$candidate")"
      if version_ge "$version" "3.11.0"; then
        PYTHON_BIN="$candidate"
        ok "Selected Python binary: $PYTHON_BIN ($version)"
        return 0
      fi
    fi
  done
  PYTHON_BIN="python3.12"
}

install_base_packages() {
  local missing=()
  local package=""
  for package in git curl ca-certificates gnupg lsb-release software-properties-common; do
    dpkg -s "$package" >/dev/null 2>&1 || missing+=("$package")
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    ok "Base Ubuntu packages already present"
    return 0
  fi
  confirm "Install missing base packages: ${missing[*]} ?" || fail "Base packages are required"
  run_sudo apt-get update
  run_sudo apt-get install -y "${missing[@]}"
}

install_python_if_needed() {
  if command_exists "$PYTHON_BIN"; then
    local version
    version="$(python_version_of "$PYTHON_BIN")"
    if version_ge "$version" "3.11.0"; then
      ok "Python requirement already satisfied: $PYTHON_BIN ($version)"
      return 0
    fi
  fi

  confirm "Install Python 3.12 and development packages?" || fail "Python 3.11+ is required"

  local version_id=""
  version_id="$(. /etc/os-release && printf '%s' "${VERSION_ID:-}")"
  run_sudo apt-get update
  if [[ "$version_id" == "22.04" ]]; then
    run_sudo add-apt-repository -y ppa:deadsnakes/ppa
    run_sudo apt-get update
  fi
  run_sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
  PYTHON_BIN="python3.12"
  ok "Installed Python via apt: $PYTHON_BIN"
}

ensure_uv() {
  if command_exists uv; then
    ok "uv already installed: $(uv --version)"
    export PATH="$HOME/.local/bin:$PATH"
    return 0
  fi
  confirm "Install uv into \$HOME/.local/bin ?" || fail "uv is required"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  command_exists uv || fail "uv installation completed but uv is still not on PATH"
  ok "uv installed: $(uv --version)"
}

docker_cli_ready() {
  command_exists docker
}

docker_daemon_ready() {
  docker info >/dev/null 2>&1
}

docker_group_has_user() {
  getent group docker >/dev/null 2>&1 && getent group docker | grep -Eq "(^|:)[^:]*:[^:]*:([^,]*,)*${USER}(,|$)"
}

docker_access_works_now() {
  docker info >/dev/null 2>&1
}

run_with_docker_group() {
  local command_string="$1"
  if docker_access_works_now; then
    bash -lc "$command_string"
    return 0
  fi
  if docker_group_has_user; then
    sg docker -c "$command_string"
    return 0
  fi
  fail "Docker access is not available for user $USER"
}

install_docker() {
  if docker_cli_ready; then
    ok "Docker CLI already present: $(docker --version)"
  else
    confirm "Install Docker Engine from Docker's official Ubuntu repository?" || fail "Docker Engine is required"
    run_sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | run_sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    run_sudo chmod a+r /etc/apt/keyrings/docker.gpg
    # shellcheck disable=SC1091
    local codename
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' \
      "$(dpkg --print-architecture)" "$codename" | run_sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    run_sudo apt-get update
    run_sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi

  if command_exists systemctl; then
    if ! systemctl is-active --quiet docker; then
      confirm "Start and enable docker.service ?" || fail "docker.service must be running"
      run_sudo systemctl enable --now docker
    else
      ok "docker.service is already active"
    fi
  fi

  if ! docker_group_has_user; then
    confirm "Add user $USER to the docker group?" || fail "User must be in docker group for Softnix sandbox mode"
    run_sudo usermod -aG docker "$USER"
    warn "docker group membership was updated during this run; the installer will continue by invoking Docker through 'sg docker'."
  elif ! id -nG 2>/dev/null | tr ' ' '\n' | grep -Fxq docker; then
    warn "User is listed in docker group but current shell has not picked it up; the installer will continue with 'sg docker'."
  fi

  run_with_docker_group "docker info >/dev/null"
  ok "Docker daemon is reachable"
}

install_node_if_needed() {
  if [[ $WITH_WHATSAPP -eq 0 ]]; then
    return 0
  fi

  if command_exists node && command_exists npm; then
    local node_version
    node_version="$(node --version | sed 's/^v//')"
    if version_ge "$node_version" "20.0.0"; then
      ok "Node.js already satisfies the WhatsApp bridge requirement: v$node_version"
      return 0
    fi
  fi

  confirm "Install Node.js 20 and npm for the WhatsApp bridge?" || fail "Node.js 20+ is required for --with-whatsapp"
  run_sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | run_sudo gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
  printf 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main\n' \
    | run_sudo tee /etc/apt/sources.list.d/nodesource.list >/dev/null
  run_sudo apt-get update
  run_sudo apt-get install -y nodejs
  ok "Installed Node.js: $(node --version), npm $(npm --version)"
}

run_setup_script() {
  local args=(--ubuntu --sandbox --python "$PYTHON_BIN" --skip-check)
  if [[ $WITH_WHATSAPP -eq 1 ]]; then
    args+=(--with-whatsapp)
  fi
  if [[ $WITH_PLAYWRIGHT_DEPS -eq 1 ]]; then
    args+=(--with-playwright-deps)
  elif [[ $WITH_PLAYWRIGHT -eq 1 ]]; then
    args+=(--with-playwright)
  fi

  step "Installing project dependencies into $VENV_DIR"
  confirm "Create/update the project virtualenv and install nanobot into $VENV_DIR ?" || fail "Project installation is required"
  bash "$ROOT_DIR/scripts/setup_linux_host.sh" "${args[@]}"
}

ensure_default_config() {
  if [[ -f "$DEFAULT_CONFIG_PATH" ]]; then
    ok "Default nanobot config already exists at $DEFAULT_CONFIG_PATH"
    return 0
  fi
  step "Creating default nanobot config"
  "$VENV_DIR/bin/nanobot" onboard
}

resolve_source_config() {
  if [[ -n "$SOURCE_CONFIG" ]]; then
    SOURCE_CONFIG="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$SOURCE_CONFIG")"
    [[ -f "$SOURCE_CONFIG" ]] || fail "Source config not found: $SOURCE_CONFIG"
    ok "Using explicit source config: $SOURCE_CONFIG"
    return 0
  fi

  if [[ -f "$DEFAULT_CONFIG_PATH" ]]; then
    SOURCE_CONFIG="$DEFAULT_CONFIG_PATH"
    ok "Using default source config: $SOURCE_CONFIG"
  else
    SOURCE_CONFIG=""
    warn "No source config found. The installer will bootstrap the instance from schema defaults."
  fi
}

build_sandbox_image() {
  local image_present=0
  if [[ $REBUILD_IMAGE -eq 0 ]]; then
    if run_with_docker_group "docker image inspect '$SANDBOX_IMAGE' >/dev/null 2>&1"; then
      image_present=1
    fi
  fi
  if [[ $image_present -eq 1 ]]; then
    ok "Sandbox image already exists: $SANDBOX_IMAGE"
    return 0
  fi
  confirm "Build sandbox image $SANDBOX_IMAGE from $ROOT_DIR/Dockerfile ?" || fail "Sandbox image is required"
  step "Building sandbox image $SANDBOX_IMAGE"
  run_with_docker_group "cd '$ROOT_DIR' && docker build -t '$SANDBOX_IMAGE' ."
}

apply_provider_config_if_requested() {
  local config_path="$1"
  if [[ -z "$PROVIDER_NAME" && -z "$PROVIDER_MODEL" && -z "$PROVIDER_API_KEY" && -z "$PROVIDER_API_BASE" ]]; then
    return 0
  fi

  step "Writing provider settings into $config_path"
  INSTALL_CONFIG_PATH="$config_path" \
  INSTALL_PROVIDER_NAME="$PROVIDER_NAME" \
  INSTALL_PROVIDER_MODEL="$PROVIDER_MODEL" \
  INSTALL_PROVIDER_API_KEY="$PROVIDER_API_KEY" \
  INSTALL_PROVIDER_API_BASE="$PROVIDER_API_BASE" \
  "$VENV_DIR/bin/python" - <<'PY'
import os
from pathlib import Path

from nanobot.config.loader import load_config, save_config

config_path = Path(os.environ["INSTALL_CONFIG_PATH"]).expanduser()
provider_name = os.environ.get("INSTALL_PROVIDER_NAME", "").strip()
provider_model = os.environ.get("INSTALL_PROVIDER_MODEL", "").strip()
provider_api_key = os.environ.get("INSTALL_PROVIDER_API_KEY", "").strip()
provider_api_base = os.environ.get("INSTALL_PROVIDER_API_BASE", "").strip()

config = load_config(config_path)

if provider_model:
    config.agents.defaults.model = provider_model
if provider_name:
    if not hasattr(config.providers, provider_name):
        raise SystemExit(f"Unknown provider name: {provider_name}")
    config.agents.defaults.provider = provider_name
    provider = getattr(config.providers, provider_name)
    if provider_api_key:
        provider.api_key = provider_api_key
    if provider_api_base:
        provider.api_base = provider_api_base
elif provider_api_base:
    config.providers.custom.api_base = provider_api_base

save_config(config, config_path)
PY
  ok "Updated provider settings in $config_path"
}

bootstrap_instance() {
  local nanobot_bin="$VENV_DIR/bin/nanobot"
  local registry_path="$SOFTNIX_HOME/admin/instances.json"
  local instance_home="$SOFTNIX_HOME/instances/$INSTANCE_ID"
  local instance_exists=0

  if [[ -d "$instance_home" ]]; then
    instance_exists=1
  fi

  if [[ $instance_exists -eq 1 && $FORCE_INSTANCE -eq 0 ]]; then
    ok "Instance already exists; reusing $instance_home"
  else
    local args=(
      softnix-init
      --instance-id "$INSTANCE_ID"
      --name "$INSTANCE_NAME"
      --owner "$OWNER"
      --env "$INSTANCE_ENV"
      --home "$SOFTNIX_HOME"
      --repo-root "$ROOT_DIR"
      --nanobot-bin "$nanobot_bin"
      --force
    )
    if [[ -n "$SOURCE_CONFIG" ]]; then
      args+=(--source-config "$SOURCE_CONFIG")
    fi

    step "Bootstrapping Softnix instance $INSTANCE_ID"
    "$nanobot_bin" "${args[@]}"
  fi

  local instance_config="$instance_home/config.json"
  [[ -f "$instance_config" ]] || fail "Expected instance config at $instance_config"

  if [[ -n "$GATEWAY_PORT" ]]; then
    step "Applying explicit gateway port $GATEWAY_PORT"
    "$VENV_DIR/bin/python" - <<PY
from pathlib import Path
from nanobot.admin.layout import update_softnix_instance, get_softnix_registry_path

update_softnix_instance(
    registry_path=Path("$registry_path").expanduser(),
    instance_id="$INSTANCE_ID",
    gateway_port=int("$GATEWAY_PORT"),
)
PY
  fi

  "$VENV_DIR/bin/python" - <<PY
from pathlib import Path
from nanobot.admin.layout import update_softnix_instance

update_softnix_instance(
    registry_path=Path("$registry_path").expanduser(),
    instance_id="$INSTANCE_ID",
    sandbox_profile="$SANDBOX_PROFILE",
    sandbox_image="$SANDBOX_IMAGE",
)
PY

  apply_provider_config_if_requested "$instance_config"
  ok "Instance bootstrap complete: $instance_home"
}

config_has_provider() {
  local config_path="$1"
  "$VENV_DIR/bin/python" - "$config_path" <<'PY'
from pathlib import Path
import sys

from nanobot.config.loader import load_config

config = load_config(Path(sys.argv[1]).expanduser())
provider = config.get_provider(config.agents.defaults.model)
provider_name = config.get_provider_name(config.agents.defaults.model)

if provider_name and getattr(provider, "api_key", None):
    raise SystemExit(0)
if provider_name in {"openai_codex", "github_copilot"}:
    raise SystemExit(0)
raise SystemExit(1)
PY
}

admin_health_ok() {
  curl -fsS "http://$ADMIN_HOST:$ADMIN_PORT/admin/health" >/dev/null 2>&1
}

wait_for_admin() {
  local attempts=30
  while [[ $attempts -gt 0 ]]; do
    if admin_health_ok; then
      ok "Softnix Admin is reachable on http://$ADMIN_HOST:$ADMIN_PORT"
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 1
  done
  return 1
}

start_admin() {
  local registry_path="$SOFTNIX_HOME/admin/instances.json"
  local log_dir="$SOFTNIX_HOME/admin/logs"
  local run_dir="$SOFTNIX_HOME/admin/run"
  local log_file="$log_dir/admin.log"
  local err_file="$log_dir/admin.err.log"
  local pid_file="$run_dir/admin.pid"
  mkdir -p "$log_dir" "$run_dir"

  if admin_health_ok; then
    ok "Softnix Admin is already running on http://$ADMIN_HOST:$ADMIN_PORT"
    return 0
  fi

  if [[ -f "$pid_file" ]]; then
    local existing_pid
    existing_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" 2>/dev/null; then
      warn "Existing admin process found (PID $existing_pid) but health check failed; terminating before restart."
      kill "$existing_pid" 2>/dev/null || true
      sleep 1
    fi
    rm -f "$pid_file"
  fi

  step "Starting Softnix Admin"
  run_with_docker_group "nohup '$VENV_DIR/bin/nanobot' softnix-admin --host '$ADMIN_HOST' --port '$ADMIN_PORT' --registry '$registry_path' >>'$log_file' 2>>'$err_file' & echo \$! > '$pid_file'"
  wait_for_admin || fail "Softnix Admin did not become healthy. Check $err_file"
}

wait_for_instance_status() {
  local status_script="$1"
  local attempts=30
  while [[ $attempts -gt 0 ]]; do
    if run_with_docker_group "'$status_script' >/dev/null 2>&1"; then
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 1
  done
  return 1
}

start_instance_if_possible() {
  local instance_home="$SOFTNIX_HOME/instances/$INSTANCE_ID"
  local config_path="$instance_home/config.json"
  local start_script="$instance_home/scripts/start.sh"
  local status_script="$instance_home/scripts/status.sh"

  if [[ $SKIP_INSTANCE_START -eq 1 ]]; then
    warn "Skipping instance start because --skip-instance-start was requested"
    return 0
  fi

  if ! config_has_provider "$config_path"; then
    warn "No usable provider credentials detected in $config_path"
    warn "Softnix Admin will be available, but the instance was not started because gateway startup would fail without provider configuration."
    return 0
  fi

  step "Starting Softnix instance $INSTANCE_ID"
  run_with_docker_group "'$start_script'"
  wait_for_instance_status "$status_script" || fail "Instance $INSTANCE_ID did not report running state"
  ok "Instance $INSTANCE_ID is running"
}

open_browser_if_possible() {
  local url="http://$ADMIN_HOST:$ADMIN_PORT"
  if [[ $NO_BROWSER -eq 1 ]]; then
    warn "Browser launch skipped by request. Open $url manually."
    return 0
  fi

  if command_exists xdg-open; then
    nohup xdg-open "$url" >/dev/null 2>&1 &
    ok "Opened browser: $url"
    return 0
  fi
  if command_exists sensible-browser; then
    nohup sensible-browser "$url" >/dev/null 2>&1 &
    ok "Opened browser: $url"
    return 0
  fi

  warn "No browser launcher found. Open $url manually."
}

run_final_checks() {
  local args=(--ubuntu --sandbox)
  if [[ $WITH_WHATSAPP -eq 1 ]]; then
    args+=(--require-node)
  fi
  if [[ $WITH_PLAYWRIGHT -eq 1 || $WITH_PLAYWRIGHT_DEPS -eq 1 ]]; then
    args+=(--require-playwright)
  fi
  step "Running final host verification"
  bash "$ROOT_DIR/scripts/check_linux_host.sh" "${args[@]}"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --instance-id)
        INSTANCE_ID="$2"
        shift 2
        ;;
      --name)
        INSTANCE_NAME="$2"
        shift 2
        ;;
      --owner)
        OWNER="$2"
        shift 2
        ;;
      --env)
        INSTANCE_ENV="$2"
        shift 2
        ;;
      --softnix-home)
        SOFTNIX_HOME="$2"
        shift 2
        ;;
      --admin-host)
        ADMIN_HOST="$2"
        shift 2
        ;;
      --admin-port)
        ADMIN_PORT="$2"
        shift 2
        ;;
      --gateway-port)
        GATEWAY_PORT="$2"
        shift 2
        ;;
      --python)
        PYTHON_BIN="$2"
        shift 2
        ;;
      --source-config)
        SOURCE_CONFIG="$2"
        shift 2
        ;;
      --sandbox-profile)
        SANDBOX_PROFILE="$2"
        shift 2
        ;;
      --sandbox-image)
        SANDBOX_IMAGE="$2"
        shift 2
        ;;
      --with-whatsapp)
        WITH_WHATSAPP=1
        shift
        ;;
      --with-playwright)
        WITH_PLAYWRIGHT=1
        shift
        ;;
      --with-playwright-deps)
        WITH_PLAYWRIGHT=1
        WITH_PLAYWRIGHT_DEPS=1
        shift
        ;;
      --provider)
        PROVIDER_NAME="$2"
        shift 2
        ;;
      --model)
        PROVIDER_MODEL="$2"
        shift 2
        ;;
      --api-key)
        PROVIDER_API_KEY="$2"
        shift 2
        ;;
      --api-base)
        PROVIDER_API_BASE="$2"
        shift 2
        ;;
      --force-instance)
        FORCE_INSTANCE=1
        shift
        ;;
      --rebuild-image)
        REBUILD_IMAGE=1
        shift
        ;;
      --skip-admin-start)
        SKIP_ADMIN_START=1
        shift
        ;;
      --skip-instance-start)
        SKIP_INSTANCE_START=1
        shift
        ;;
      --no-browser)
        NO_BROWSER=1
        shift
        ;;
      -y|--yes)
        AUTO_YES=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "Unknown option: $1"
        ;;
    esac
  done
}

print_plan() {
  step "Execution plan"
  log "Repo root         : $ROOT_DIR"
  log "Python binary     : ${PYTHON_BIN:-auto-detect}"
  log "Softnix home      : $SOFTNIX_HOME"
  log "Instance          : $INSTANCE_ID ($INSTANCE_NAME)"
  log "Owner / Env       : $OWNER / $INSTANCE_ENV"
  log "Admin endpoint    : http://$ADMIN_HOST:$ADMIN_PORT"
  log "Sandbox profile   : $SANDBOX_PROFILE"
  log "Sandbox image     : $SANDBOX_IMAGE"
  log "Source config     : ${SOURCE_CONFIG:-auto}"
  log "WhatsApp bridge   : $([[ $WITH_WHATSAPP -eq 1 ]] && echo enabled || echo disabled)"
  log "Playwright        : $([[ $WITH_PLAYWRIGHT -eq 1 ]] && echo enabled || echo disabled)"
}

main() {
  parse_args "$@"
  require_ubuntu
  select_python
  print_plan

  step "Initial host report"
  bash "$ROOT_DIR/scripts/check_linux_host.sh" --ubuntu --sandbox || true

  install_base_packages
  install_python_if_needed
  ensure_uv
  install_docker
  install_node_if_needed
  run_setup_script
  ensure_default_config
  resolve_source_config
  build_sandbox_image
  bootstrap_instance
  run_final_checks

  if [[ $SKIP_ADMIN_START -eq 0 ]]; then
    start_admin
  else
    warn "Skipping Softnix Admin startup because --skip-admin-start was requested"
  fi

  start_instance_if_possible

  if [[ $SKIP_ADMIN_START -eq 0 ]]; then
    open_browser_if_possible
  fi

  step "Installation completed"
  log "Softnix Admin : http://$ADMIN_HOST:$ADMIN_PORT"
  log "Registry      : $SOFTNIX_HOME/admin/instances.json"
  log "Instance home : $SOFTNIX_HOME/instances/$INSTANCE_ID"
  log "Admin logs    : $SOFTNIX_HOME/admin/logs/admin.log"
}

main "$@"
