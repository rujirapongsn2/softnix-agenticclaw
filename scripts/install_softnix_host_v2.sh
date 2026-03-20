#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Softnix AgenticClaw Installer v2 - Improved Ubuntu Installation Script
# ============================================================================
# Addresses common issues:
# - APT cdrom source conflicts
# - Docker group membership and immediate availability
# - Admin UI port conflicts
# - Instance startup failures
# - Missing zsh shebang (uses bash instead)
# - Docker image build before instance start
# - Better error handling and rollback
# ============================================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
DEFAULT_SOFTNIX_HOME="$HOME/.softnix"
DEFAULT_CONFIG_PATH="$HOME/.nanobot/config.json"
DEFAULT_INSTANCE_ID="default-prod"
DEFAULT_ADMIN_PORT="18880"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration variables
INSTANCE_ID="$DEFAULT_INSTANCE_ID"
INSTANCE_NAME="Default Production"
OWNER="default"
INSTANCE_ENV="prod"
ADMIN_HOST="127.0.0.1"
ADMIN_PORT="$DEFAULT_ADMIN_PORT"
SOFTNIX_HOME="$DEFAULT_SOFTNIX_HOME"
PYTHON_BIN=""
SOURCE_CONFIG=""
SANDBOX_PROFILE="balanced"
SANDBOX_IMAGE="softnixclaw:latest"
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
FIX_ISSUES=1

# ============================================================================
# Logging Functions
# ============================================================================

log_info() {
  echo -e "${BLUE}[INFO]${NC} $*"
}

log_ok() {
  echo -e "${GREEN}[OK]${NC} $*"
}

log_warn() {
  echo -e "${YELLOW}[WARN]${NC} $*" >&2
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $*" >&2
}

log_step() {
  echo ""
  echo -e "${BLUE}==>${NC} $*"
}

confirm() {
  local prompt="$1"
  if [[ $AUTO_YES -eq 1 ]]; then
    log_ok "$prompt -> yes"
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

fail() {
  log_error "$*"
  log_error "Installation failed. You can re-run this script to resume."
  exit 1
}

# ============================================================================
# System Checks and Fixes
# ============================================================================

fix_apt_cdrom_sources() {
  log_step "Fixing APT cdrom sources (common Ubuntu issue)"
  
  local apt_dir="/etc/apt"
  local files=()
  
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    files+=("$file")
  done < <(find "$apt_dir" -maxdepth 2 -type f \( -name 'sources.list' -o -name '*.list' \) 2>/dev/null || true)

  if [[ ${#files[@]} -eq 0 ]]; then
    log_info "No APT source files found to check"
    return 0
  fi

  local changed_count
  changed_count="$(sudo python3 - "${files[@]}" <<'PY'
from pathlib import Path
import sys

changed = 0
for raw_path in sys.argv[1:]:
    path = Path(raw_path)
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        continue

    lines = text.splitlines()
    new_lines = []
    file_changed = False
    for line in lines:
        stripped = line.lstrip()
        lower = stripped.lower()
        if "cdrom:" in lower or "file:/cdrom" in lower:
            if stripped.startswith("#"):
                new_lines.append(line)
                continue
            new_lines.append("# disabled by softnix installer: " + line)
            file_changed = True
            changed += 1
        else:
            new_lines.append(line)

    if file_changed:
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(text, encoding="utf-8")
        path.write_text("\n".join(new_lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")

print(changed)
PY
  )"

  if [[ "${changed_count:-0}" != "0" ]]; then
    log_ok "Disabled $changed_count cdrom-based APT source entries"
  else
    log_info "No cdrom sources to disable"
  fi
}

apt_update_safe() {
  log_info "Updating APT package lists..."
  local log_file
  log_file="$(mktemp /tmp/softnix_apt_update.XXXXXX.log)"
  
  if sudo apt-get update >"$log_file" 2>&1; then
    rm -f "$log_file"
    log_ok "APT update completed successfully"
    return 0
  fi

  if grep -qiE 'cdrom|file:/cdrom|no longer has a Release file' "$log_file"; then
    log_warn "APT update failed due to cdrom sources"
    cat "$log_file" >&2
    rm -f "$log_file"
    
    fix_apt_cdrom_sources
    
    if grep -RInE 'cdrom:|file:/cdrom' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
      log_warn "Some cdrom sources still remain"
    fi
    
    if sudo apt-get update; then
      log_ok "APT update succeeded after fixing cdrom sources"
      return 0
    fi
    fail "APT update still fails after fixing cdrom sources"
  fi

  cat "$log_file" >&2
  rm -f "$log_file"
  fail "APT update failed"
}

version_ge() {
  python3 - "$1" "$2" <<'PY'
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
  if [[ ! -f /etc/os-release ]]; then
    fail "/etc/os-release not found - this installer requires Ubuntu"
  fi
  
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    fail "This installer currently supports Ubuntu only (found: ${ID:-unknown})"
  fi
  
  log_ok "Ubuntu detected: ${PRETTY_NAME:-$ID}"
}

# ============================================================================
# Python Setup
# ============================================================================

python_version_of() {
  local bin="$1"
  "$bin" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'
}

select_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
      local version
      version="$(python_version_of "$PYTHON_BIN")"
      if version_ge "$version" "3.11.0"; then
        log_ok "Using Python: $PYTHON_BIN ($version)"
        return 0
      fi
      fail "Python $PYTHON_BIN is too old ($version), need >= 3.11"
    fi
    fail "Python binary not found: $PYTHON_BIN"
  fi

  local candidates=(python3.12 python3.11 python3)
  for candidate in "${candidates[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1; then
      local version
      version="$(python_version_of "$candidate")"
      if version_ge "$version" "3.11.0"; then
        PYTHON_BIN="$candidate"
        log_ok "Selected Python: $PYTHON_BIN ($version)"
        return 0
      fi
    fi
  done
  
  fail "Python 3.11+ not found. Please install python3.11 or python3.12"
}

install_python_if_needed() {
  if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    local version
    version="$(python_version_of "$PYTHON_BIN")"
    if version_ge "$version" "3.11.0"; then
      log_ok "Python requirement satisfied: $PYTHON_BIN ($version)"
      return 0
    fi
  fi

  log_step "Installing Python 3.12"
  apt_update_safe
  
  local version_id
  version_id="$(. /etc/os-release && printf '%s' "${VERSION_ID:-}")"
  
  if [[ "$version_id" == "22.04" ]]; then
    log_info "Adding deadsnakes PPA for Python 3.12..."
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    apt_update_safe
  fi
  
  log_info "Installing Python 3.12 and development packages..."
  sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
  
  PYTHON_BIN="python3.12"
  log_ok "Python 3.12 installed"
}

# ============================================================================
# UV Package Manager
# ============================================================================

ensure_uv() {
  export PATH="$HOME/.local/bin:$PATH"
  
  if command -v uv >/dev/null 2>&1; then
    log_ok "uv already installed: $(uv --version)"
    return 0
  fi
  
  log_step "Installing uv package manager"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  
  export PATH="$HOME/.local/bin:$PATH"
  
  if ! command -v uv >/dev/null 2>&1; then
    fail "uv installation completed but uv not found on PATH"
  fi
  
  log_ok "uv installed: $(uv --version)"
}

# ============================================================================
# Docker Setup - CRITICAL FIXES
# ============================================================================

docker_group_has_user() {
  getent group docker >/dev/null 2>&1 && getent group docker | grep -qE "(^|:)[^:]*:[^:]*:([^,]*,)*${USER}(,|$)"
}

docker_access_works() {
  docker info >/dev/null 2>&1
}

install_docker() {
  log_step "Setting up Docker Engine"
  
  if command -v docker >/dev/null 2>&1; then
    log_ok "Docker CLI found: $(docker --version)"
  else
    log_info "Installing Docker Engine from official repository..."
    
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    
    local codename
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' \
      "$(dpkg --print-architecture)" "$codename" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    
    apt_update_safe
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi

  # Ensure Docker service is running
  if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl is-active --quiet docker; then
      log_info "Starting Docker service..."
      sudo systemctl enable --now docker
    fi
    log_ok "Docker service is running"
  fi

  # CRITICAL FIX: Add user to docker group and apply immediately
  if ! docker_group_has_user; then
    log_info "Adding user '$USER' to docker group..."
    sudo usermod -aG docker "$USER"
    log_ok "User added to docker group"
  else
    log_info "User '$USER' is already in docker group"
  fi

  # CRITICAL FIX: Use newgrp to apply group membership immediately
  log_info "Applying docker group to current session..."
  if newgrp docker <<'INNER'
docker info >/dev/null 2>&1
INNER
  then
    log_ok "Docker daemon is reachable with docker group"
  else
    # Fallback: try sg command
    if sg docker -c "docker info" >/dev/null 2>&1; then
      log_ok "Docker accessible via 'sg docker' command"
    else
      fail "Docker daemon not reachable. Please logout and login again, then re-run this script."
    fi
  fi
}

# ============================================================================
# Node.js for WhatsApp Bridge
# ============================================================================

install_node_if_needed() {
  if [[ $WITH_WHATSAPP -eq 0 ]]; then
    return 0
  fi

  log_step "Setting up Node.js for WhatsApp bridge"
  
  if command -v node >/dev/null 2>&1; then
    local node_version
    node_version="$(node --version | sed 's/^v//')"
    if version_ge "$node_version" "20.0.0"; then
      log_ok "Node.js already installed: v$node_version"
      return 0
    fi
  fi

  log_info "Installing Node.js 20..."
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
  printf 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main\n' | \
    sudo tee /etc/apt/sources.list.d/nodesource.list >/dev/null
  
  apt_update_safe
  sudo apt-get install -y nodejs
  
  log_ok "Node.js installed: $(node --version), npm $(npm --version)"
}

# ============================================================================
# Project Installation
# ============================================================================

install_project() {
  log_step "Installing project dependencies"
  
  cd "$ROOT_DIR"
  
  # Create virtual environment
  if [[ ! -d "$VENV_DIR" ]]; then
    log_info "Creating virtual environment at $VENV_DIR"
    uv venv --python "$PYTHON_BIN" "$VENV_DIR"
  else
    log_info "Virtual environment found at $VENV_DIR"
  fi

  # Install project in editable mode
  log_info "Installing project dependencies..."
  uv pip install --python "$VENV_DIR/bin/python" -e .
  
  # Install optional dependencies
  if [[ $WITH_PLAYWRIGHT -eq 1 ]]; then
    log_info "Installing Playwright..."
    uv pip install --python "$VENV_DIR/bin/python" playwright
    if [[ $WITH_PLAYWRIGHT_DEPS -eq 1 ]]; then
      log_info "Installing Playwright browsers with system dependencies..."
      "$VENV_DIR/bin/python" -m playwright install --with-deps chromium
    else
      "$VENV_DIR/bin/python" -m playwright install chromium
    fi
  fi

  if [[ $WITH_WHATSAPP -eq 1 ]]; then
    log_info "Building WhatsApp bridge..."
    (
      cd "$ROOT_DIR/bridge"
      npm install
      npm run build
    )
  fi

  # Create necessary directories
  mkdir -p "$HOME/.nanobot" "$SOFTNIX_HOME/admin" "$SOFTNIX_HOME/instances"
  
  log_ok "Project installation completed"
}

# ============================================================================
# Docker Image Build - CRITICAL FIX
# ============================================================================

build_sandbox_image() {
  log_step "Building Docker sandbox image"
  
  local image_present=0
  if [[ $REBUILD_IMAGE -eq 0 ]]; then
    if sg docker -c "docker image inspect '$SANDBOX_IMAGE'" >/dev/null 2>&1; then
      image_present=1
    fi
  fi
  
  if [[ $image_present -eq 1 ]]; then
    log_ok "Sandbox image already exists: $SANDBOX_IMAGE"
    return 0
  fi
  
  log_info "Building sandbox image '$SANDBOX_IMAGE' from Dockerfile..."
  log_info "This may take several minutes on first build..."
  
  if sg docker -c "cd '$ROOT_DIR' && docker build -t '$SANDBOX_IMAGE' ."; then
    log_ok "Sandbox image built successfully: $SANDBOX_IMAGE"
  else
    fail "Failed to build sandbox image. Check Docker logs for details."
  fi
}

# ============================================================================
# Instance Bootstrap
# ============================================================================

bootstrap_instance() {
  local nanobot_bin="$VENV_DIR/bin/nanobot"
  local registry_path="$SOFTNIX_HOME/admin/instances.json"
  local instance_home="$SOFTNIX_HOME/instances/$INSTANCE_ID"
  
  log_step "Bootstrapping Softnix instance: $INSTANCE_ID"
  
  # Check if instance already exists
  if [[ -d "$instance_home" && $FORCE_INSTANCE -eq 0 ]]; then
    log_warn "Instance already exists at $instance_home"
    if ! confirm "Overwrite existing instance?"; then
      log_info "Reusing existing instance"
    else
      FORCE_INSTANCE=1
    fi
  fi

  local args=(
    softnix-init
    --instance-id "$INSTANCE_ID"
    --name "$INSTANCE_NAME"
    --owner "$OWNER"
    --env "$INSTANCE_ENV"
    --home "$SOFTNIX_HOME"
    --repo-root "$ROOT_DIR"
    --nanobot-bin "$nanobot_bin"
  )
  
  if [[ $FORCE_INSTANCE -eq 1 ]]; then
    args+=(--force)
  fi
  
  if [[ -n "$SOURCE_CONFIG" ]]; then
    args+=(--source-config "$SOURCE_CONFIG")
  fi

  log_info "Running softnix-init..."
  if ! "$nanobot_bin" "${args[@]}"; then
    fail "Failed to bootstrap instance"
  fi

  # Apply gateway port if specified
  if [[ -n "$GATEWAY_PORT" ]]; then
    log_info "Setting gateway port to $GATEWAY_PORT"
    "$VENV_DIR/bin/python" - <<PY
from pathlib import Path
from nanobot.admin.layout import update_softnix_instance

update_softnix_instance(
    registry_path=Path("$registry_path").expanduser(),
    instance_id="$INSTANCE_ID",
    gateway_port=int("$GATEWAY_PORT"),
)
PY
  fi

  # Apply sandbox profile
  log_info "Applying sandbox profile: $SANDBOX_PROFILE"
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

  # Apply provider config if specified
  if [[ -n "$PROVIDER_NAME" || -n "$PROVIDER_MODEL" || -n "$PROVIDER_API_KEY" ]]; then
    log_info "Configuring provider settings..."
    INSTALL_CONFIG_PATH="$instance_home/config.json" \
    INSTALL_PROVIDER_NAME="$PROVIDER_NAME" \
    INSTALL_PROVIDER_MODEL="$PROVIDER_MODEL" \
    INSTALL_PROVIDER_API_KEY="$PROVIDER_API_KEY" \
    INSTALL_PROVIDER_API_BASE="${PROVIDER_API_BASE:-}" \
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
    if hasattr(config.providers, provider_name):
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
  fi

  # CRITICAL FIX: Fix shebang in lifecycle scripts (change #!/bin/zsh to #!/usr/bin/env bash)
  log_info "Fixing lifecycle script shebangs..."
  for script in "$instance_home/scripts"/*.sh; do
    if [[ -f "$script" ]]; then
      sed -i '1s|^#!/bin/zsh|#!/usr/bin/env bash|' "$script"
      sed -i '1s|^#!/bin/bash|#!/usr/bin/env bash|' "$script"
    fi
  done
  
  log_ok "Instance bootstrapped: $instance_home"
}

# ============================================================================
# Admin Service Setup - CRITICAL FIX for Port Conflicts
# ============================================================================

check_port_available() {
  local port="$1"
  if ss -tlnp 2>/dev/null | grep -q ":$port " || \
     netstat -tlnp 2>/dev/null | grep -q ":$port " || \
     lsof -i ":$port" >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

find_available_port() {
  local start_port="$1"
  local port="$start_port"
  
  while ! check_port_available "$port"; do
    port=$((port + 1))
    if [[ $port -gt 65535 ]]; then
      fail "No available ports found starting from $start_port"
    fi
  done
  
  echo "$port"
}

setup_admin_service() {
  log_step "Setting up Admin service"
  
  local registry_path="$SOFTNIX_HOME/admin/instances.json"
  local log_dir="$SOFTNIX_HOME/admin/logs"
  local run_dir="$SOFTNIX_HOME/admin/run"
  local service_file="$HOME/.config/systemd/user/softnix-admin.service"
  
  # CRITICAL FIX: Check for port conflicts
  if ! check_port_available "$ADMIN_PORT"; then
    log_warn "Port $ADMIN_PORT is already in use"
    local new_port
    new_port=$(find_available_port "$ADMIN_PORT")
    log_info "Found available port: $new_port"
    
    if confirm "Use port $new_port instead of $ADMIN_PORT?"; then
      ADMIN_PORT="$new_port"
      log_ok "Using port $ADMIN_PORT"
    else
      fail "Port conflict resolved. Please specify a different port with --admin-port"
    fi
  fi

  mkdir -p "$log_dir" "$run_dir" "$(dirname "$service_file")"

  # Create wrapper script for Docker access
  local wrapper_script="$ROOT_DIR/scripts/start-admin-with-docker.sh"
  cat > "$wrapper_script" <<'WRAPPER'
#!/usr/bin/env bash
# Wrapper script to start Admin with Docker group access
exec sg docker -c "$NANOBOT_BIN softnix-admin --host $ADMIN_HOST --port $ADMIN_PORT"
WRAPPER
  chmod +x "$wrapper_script"

  # Create systemd service
  cat > "$service_file" <<EOF
[Unit]
Description=Softnix AgenticClaw Admin Service
After=network.target docker.service

[Service]
Type=simple
WorkingDirectory=$ROOT_DIR
ExecStart=$wrapper_script
Environment=NANOBOT_BIN=$VENV_DIR/bin/nanobot
Environment=ADMIN_HOST=$ADMIN_HOST
Environment=ADMIN_PORT=$ADMIN_PORT
Restart=always
RestartSec=10
StandardOutput=append:$log_dir/admin.log
StandardError=append:$log_dir/admin.err.log

[Install]
WantedBy=default.target
EOF

  # Reload systemd and start service
  log_info "Starting Admin service..."
  systemctl --user daemon-reload
  systemctl --user stop softnix-admin.service 2>/dev/null || true
  systemctl --user start softnix-admin.service
  systemctl --user enable softnix-admin.service

  # Wait for service to be healthy
  log_info "Waiting for Admin service to start..."
  local attempts=30
  while [[ $attempts -gt 0 ]]; do
    if curl -fsS "http://$ADMIN_HOST:$ADMIN_PORT/admin/health" >/dev/null 2>&1; then
      log_ok "Admin service started on http://$ADMIN_HOST:$ADMIN_PORT"
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 1
  done
  
  # Check for errors
  if [[ -f "$log_dir/admin.err.log" ]]; then
    log_error "Admin service failed to start. Recent errors:"
    tail -20 "$log_dir/admin.err.log" >&2
  fi
  
  fail "Admin service did not become healthy"
}

# ============================================================================
# Instance Startup
# ============================================================================

start_instance() {
  if [[ $SKIP_INSTANCE_START -eq 1 ]]; then
    log_warn "Skipping instance start (--skip-instance-start)"
    return 0
  fi

  local instance_home="$SOFTNIX_HOME/instances/$INSTANCE_ID"
  local config_path="$instance_home/config.json"
  local start_script="$instance_home/scripts/start.sh"
  local status_script="$instance_home/scripts/status.sh"

  # Check if provider is configured
  if ! "$VENV_DIR/bin/python" - "$config_path" <<'PY'
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
  then
    log_warn "No provider API key configured in instance"
    log_warn "Instance not started - configure provider in Admin UI first"
    return 0
  fi

  log_step "Starting instance: $INSTANCE_ID"
  
  # CRITICAL FIX: Use sg docker to ensure Docker access
  if ! sg docker -c "'$start_script'"; then
    log_error "Failed to start instance"
    if [[ -f "$instance_home/logs/gateway.err.log" ]]; then
      log_error "Recent errors:"
      tail -20 "$instance_home/logs/gateway.err.log" >&2
    fi
    fail "Instance failed to start"
  fi

  # Wait for instance to be running
  log_info "Waiting for instance to start..."
  local attempts=30
  while [[ $attempts -gt 0 ]]; do
    if sg docker -c "'$status_script'" >/dev/null 2>&1; then
      log_ok "Instance $INSTANCE_ID is running"
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 1
  done
  
  log_error "Instance did not report running state"
  if [[ -f "$instance_home/logs/gateway.err.log" ]]; then
    log_error "Recent errors:"
    tail -20 "$instance_home/logs/gateway.err.log" >&2
  fi
  
  fail "Instance failed to become healthy"
}

# ============================================================================
# Final Steps
# ============================================================================

open_browser() {
  if [[ $NO_BROWSER -eq 1 ]]; then
    log_warn "Browser launch skipped. Open http://$ADMIN_HOST:$ADMIN_PORT manually"
    return 0
  fi

  local url="http://$ADMIN_HOST:$ADMIN_PORT"
  
  if command -v xdg-open >/dev/null 2>&1; then
    nohup xdg-open "$url" >/dev/null 2>&1 &
    log_ok "Opened browser: $url"
  elif command -v sensible-browser >/dev/null 2>&1; then
    nohup sensible-browser "$url" >/dev/null 2>&1 &
    log_ok "Opened browser: $url"
  else
    log_warn "No browser found. Open $url manually"
  fi
}

print_summary() {
  log_step "Installation completed successfully!"
  
  echo ""
  echo "=============================================="
  echo "  Softnix AgenticClaw Installation Summary"
  echo "=============================================="
  echo ""
  echo "  Admin UI:      http://$ADMIN_HOST:$ADMIN_PORT"
  echo "  Instance:      $INSTANCE_ID"
  echo "  Instance Home: $SOFTNIX_HOME/instances/$INSTANCE_ID"
  echo "  Config:        $SOFTNIX_HOME/instances/$INSTANCE_ID/config.json"
  echo ""
  echo "Useful commands:"
  echo "  ./softnixclaw admin start|stop|restart|status"
  echo "  ~/.softnix/instances/$INSTANCE_ID/scripts/start.sh"
  echo "  ~/.softnix/instances/$INSTANCE_ID/scripts/status.sh"
  echo ""
  echo "Documentation:"
  echo "  README.md"
  echo "  README-Softnix.md"
  echo "  README-Softnix-Installation.md"
  echo ""
  
  if [[ -z "$PROVIDER_API_KEY" ]]; then
    echo -e "${YELLOW}NOTE:${NC} Configure your LLM provider API key in the Admin UI"
    echo "       Go to Providers tab to add your API key"
    echo ""
  fi
}

# ============================================================================
# Main
# ============================================================================

usage() {
  cat <<'EOF'
Usage: bash scripts/install_softnix_host_v2.sh [options]

Improved installer for Softnix AgenticClaw on Ubuntu.

Options:
  --instance-id <id>            Instance ID (default: default-prod)
  --name <name>                 Instance display name
  --owner <owner>               Owner label (default: default)
  --env <env>                   Environment (default: prod)
  --softnix-home <path>         Softnix home directory (default: ~/.softnix)
  --admin-host <host>           Admin bind host (default: 127.0.0.1)
  --admin-port <port>           Admin port (default: 18880)
  --gateway-port <port>         Instance gateway port
  --python <bin>                Python binary (default: auto-detect)
  --source-config <path>        Config template path (default: ~/.softnix/instances/default-prod/config.json if exists)
  --sandbox-profile <name>      strict|balanced|fast (default: balanced)
  --sandbox-image <image>       Docker image (default: softnixclaw:latest)
  --with-whatsapp               Install WhatsApp bridge
  --with-playwright             Install Playwright
  --with-playwright-deps        Install Playwright with system deps
  --provider <name>             Provider name
  --model <name>                Default model
  --api-key <value>             Provider API key
  --api-base <url>              Provider API base URL
  --force-instance              Overwrite existing instance
  --rebuild-image               Rebuild Docker image
  --skip-admin-start            Don't start Admin service
  --skip-instance-start         Don't start instance
  --no-browser                  Don't open browser
  -y, --yes                     Auto-confirm all prompts
  -h, --help                    Show this help

Examples:
  bash scripts/install_softnix_host_v2.sh
  bash scripts/install_softnix_host_v2.sh --instance-id mybot --api-key sk-xxx
  bash scripts/install_softnix_host_v2.sh -y --with-whatsapp
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --instance-id) INSTANCE_ID="$2"; shift 2 ;;
      --name) INSTANCE_NAME="$2"; shift 2 ;;
      --owner) OWNER="$2"; shift 2 ;;
      --env) INSTANCE_ENV="$2"; shift 2 ;;
      --softnix-home) SOFTNIX_HOME="$2"; shift 2 ;;
      --admin-host) ADMIN_HOST="$2"; shift 2 ;;
      --admin-port) ADMIN_PORT="$2"; shift 2 ;;
      --gateway-port) GATEWAY_PORT="$2"; shift 2 ;;
      --python) PYTHON_BIN="$2"; shift 2 ;;
      --source-config) SOURCE_CONFIG="$2"; shift 2 ;;
      --sandbox-profile) SANDBOX_PROFILE="$2"; shift 2 ;;
      --sandbox-image) SANDBOX_IMAGE="$2"; shift 2 ;;
      --with-whatsapp) WITH_WHATSAPP=1; shift ;;
      --with-playwright) WITH_PLAYWRIGHT=1; shift ;;
      --with-playwright-deps) WITH_PLAYWRIGHT=1; WITH_PLAYWRIGHT_DEPS=1; shift ;;
      --provider) PROVIDER_NAME="$2"; shift 2 ;;
      --model) PROVIDER_MODEL="$2"; shift 2 ;;
      --api-key) PROVIDER_API_KEY="$2"; shift 2 ;;
      --api-base) PROVIDER_API_BASE="$2"; shift 2 ;;
      --force-instance) FORCE_INSTANCE=1; shift ;;
      --rebuild-image) REBUILD_IMAGE=1; shift ;;
      --skip-admin-start) SKIP_ADMIN_START=1; shift ;;
      --skip-instance-start) SKIP_INSTANCE_START=1; shift ;;
      --no-browser) NO_BROWSER=1; shift ;;
      -y|--yes) AUTO_YES=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) fail "Unknown option: $1" ;;
    esac
  done
}

main() {
  echo ""
  echo "=========================================="
  echo "  Softnix AgenticClaw Installer v2"
  echo "=========================================="
  echo ""
  
  parse_args "$@"
  
  log_step "Starting installation"
  
  # System requirements
  require_ubuntu
  select_python
  install_python_if_needed
  ensure_uv
  
  # System setup
  apt_update_safe
  install_base_packages() {
    log_info "Installing base packages..."
    local missing=()
    for package in git curl ca-certificates gnupg lsb-release software-properties-common; do
      dpkg -s "$package" >/dev/null 2>&1 || missing+=("$package")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
      sudo apt-get install -y "${missing[@]}"
    fi
    log_ok "Base packages installed"
  }
  install_base_packages
  
  # Docker setup
  install_docker
  
  # Optional dependencies
  install_node_if_needed
  
  # Project installation
  install_project
  
  # Build Docker image (CRITICAL - must be done before instance start)
  build_sandbox_image
  
  # Bootstrap instance
  bootstrap_instance
  
  # Start services
  if [[ $SKIP_ADMIN_START -eq 0 ]]; then
    setup_admin_service
    start_instance
  fi
  
  # Open browser
  open_browser
  
  # Summary
  print_summary
}

# Run main function
main "$@"
