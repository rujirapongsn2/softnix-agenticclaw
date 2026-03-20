# Softnix AgenticClaw Installer v2 - Improvements Guide

## Overview

Installer v2 (`install_softnix_host_v2.sh`) addresses all common installation issues encountered during fresh Ubuntu installations.

## Key Improvements

### 1. ✅ APT CDROM Source Fix

**Problem:** Ubuntu installations often have stale cdrom APT sources causing `apt-get update` to fail:
```
Err:1 cdrom://Ubuntu 24.04 LTS noble InRelease
      No longer has a Release file
```

**Solution:** 
- Automatically detects and comments out cdrom-based APT sources
- Creates backup of original files (`.bak` extension)
- Retries apt-get update after fixing

**Code:**
```bash
fix_apt_cdrom_sources() {
  # Finds and disables all cdrom:/file:/cdrom sources
  # in /etc/apt/sources.list and /etc/apt/sources.list.d/
}
```

---

### 2. ✅ Docker Group Membership - Immediate Availability

**Problem:** After adding user to docker group, changes don't take effect until logout/login:
```bash
sudo usermod -aG docker "$USER"
# Still can't run docker without sudo!
```

**Solution:**
- Uses `newgrp docker` to apply group membership immediately
- Falls back to `sg docker -c` command if newgrp fails
- All Docker commands use `sg docker -c` wrapper

**Code:**
```bash
# Add user to docker group
sudo usermod -aG docker "$USER"

# Apply immediately without logout
newgrp docker <<'INNER'
docker info >/dev/null 2>&1
INNER

# All subsequent commands use:
sg docker -c "docker build ..."
sg docker -c "docker run ..."
```

---

### 3. ✅ Admin UI Port Conflict Detection

**Problem:** Port 18880 already in use causes Admin service to fail silently:
```
Unable to start Admin: Address already in use
```

**Solution:**
- Checks if port is available before binding
- Automatically finds next available port if conflict detected
- Prompts user to accept alternative port

**Code:**
```bash
check_port_available() {
  ss -tlnp | grep -q ":$PORT " && return 1
  return 0
}

find_available_port() {
  while ! check_port_available "$port"; do
    port=$((port + 1))
  done
  echo "$port"
}
```

---

### 4. ✅ Instance Startup Failures

**Problem:** Instances fail to start due to:
- Docker image not built
- Missing provider API keys
- Script shebang issues (`#!/bin/zsh` not found)

**Solutions:**

#### 4.1 Build Docker Image First
```bash
# CRITICAL: Build image BEFORE starting instance
build_sandbox_image() {
  sg docker -c "docker build -t softnixclaw:latest ."
}

# Called before bootstrap_instance
```

#### 4.2 Check Provider Configuration
```bash
# Don't start instance if no API key configured
if ! config_has_provider "$config_path"; then
  log_warn "No provider API key - instance not started"
  return 0  # Not an error, just skip
fi
```

#### 4.3 Fix Script Shebangs
```bash
# Replace #!/bin/zsh with #!/usr/bin/env bash
for script in "$instance_home/scripts"/*.sh; do
  sed -i '1s|^#!/bin/zsh|#!/usr/bin/env bash|' "$script"
done
```

---

### 5. ✅ Docker Image Auto-Build

**Problem:** Installer tries to start instances before building Docker image:
```
Unable to find image 'softnixclaw:latest' locally
docker: Error response from daemon: pull access denied
```

**Solution:**
- Explicit image build step before instance bootstrap
- Checks if image exists before rebuilding
- `--rebuild-image` flag to force rebuild

**Code:**
```bash
build_sandbox_image() {
  if [[ $REBUILD_IMAGE -eq 0 ]] && image_exists; then
    log_ok "Image already exists"
    return 0
  fi
  
  sg docker -c "docker build -t '$SANDBOX_IMAGE' ."
}

# Execution order:
install_project
build_sandbox_image      # <-- Build FIRST
bootstrap_instance       # <-- Then create instance
start_instance          # <-- Then start
```

---

### 6. ✅ Script Shebang Compatibility

**Problem:** Lifecycle scripts use `#!/bin/zsh` but Ubuntu doesn't have zsh:
```bash
bash: /home/user/.softnix/instances/prod/scripts/start.sh: /bin/zsh: bad interpreter
```

**Solution:**
- Automatically fixes shebangs during instance bootstrap
- Changes `#!/bin/zsh` → `#!/usr/bin/env bash`
- Changes `#!/bin/bash` → `#!/usr/bin/env bash` (more portable)

**Code:**
```bash
# Fix all lifecycle script shebangs
for script in "$instance_home/scripts"/*.sh; do
  sed -i '1s|^#!/bin/zsh|#!/usr/bin/env bash|' "$script"
  sed -i '1s|^#!/bin/bash|#!/usr/bin/env bash|' "$script"
done
```

---

### 7. ✅ Better Error Handling

**Problem:** Errors during installation cause confusing failures with no guidance.

**Solution:**
- Color-coded output (RED=error, GREEN=ok, YELLOW=warn, BLUE=info)
- Clear error messages with suggested fixes
- Rollback instructions on failure
- Detailed logging to files

**Example Output:**
```
[INFO] Starting installation
[OK] Ubuntu detected: Ubuntu 24.04 LTS
[OK] Selected Python: python3.12 (3.12.3)
[WARN] Port 18880 is already in use
[INFO] Found available port: 18881
[OK] Using port 18881
[ERROR] Failed to build sandbox image
[ERROR] Installation failed. You can re-run this script to resume.
```

---

### 8. ✅ Admin Service with Docker Access

**Problem:** Admin service runs under systemd but can't access Docker socket.

**Solution:**
- Creates wrapper script `start-admin-with-docker.sh`
- Wrapper uses `sg docker -c` to ensure Docker access
- Systemd service calls wrapper instead of direct command

**Code:**
```bash
# Create wrapper script
cat > "$wrapper_script" <<'WRAPPER'
#!/usr/bin/env bash
exec sg docker -c "$NANOBOT_BIN softnix-admin --host $ADMIN_HOST --port $ADMIN_PORT"
WRAPPER

# Systemd service uses wrapper
ExecStart=$wrapper_script
```

---

## Usage

### Basic Installation

```bash
# Simplest case - auto-detect everything
bash scripts/install_softnix_host_v2.sh -y
```

### With Provider Configuration

```bash
# Install with OpenAI API key
bash scripts/install_softnix_host_v2.sh \
  --api-key sk-your-openai-key \
  --model gpt-4o-mini
```

### With WhatsApp Bridge

```bash
# Install with WhatsApp support
bash scripts/install_softnix_host_v2.sh \
  --with-whatsapp \
  -y
```

### Custom Instance Configuration

```bash
# Custom instance ID and port
bash scripts/install_softnix_host_v2.sh \
  --instance-id mybot-prod \
  --name "My Production Bot" \
  --admin-port 19000 \
  --gateway-port 18800
```

### Advanced Options

```bash
# Full control installation
bash scripts/install_softnix_host_v2.sh \
  --instance-id team-a-prod \
  --owner team-a \
  --env production \
  --sandbox-profile strict \
  --with-playwright-deps \
  --provider openai \
  --model gpt-4o \
  --api-key sk-xxx \
  --api-base https://api.openai.com/v1 \
  --no-browser \
  -y
```

---

## Comparison: v1 vs v2

| Issue | v1 Behavior | v2 Behavior |
|-------|-------------|-------------|
| **APT cdrom sources** | Fails with cryptic error | Auto-fixes and continues |
| **Docker group** | Requires logout/login | Works immediately with `newgrp` |
| **Port conflicts** | Silent failure | Detects and suggests alternative |
| **Docker image** | Tries to pull from Docker Hub | Builds locally before use |
| **Script shebang** | `/bin/zsh` not found | Auto-fixes to `/usr/bin/env bash` |
| **Provider config** | Instance fails to start | Warns and skips start |
| **Error messages** | Generic failures | Detailed with suggestions |
| **Admin Docker access** | Permission denied | Wrapper script with `sg docker` |

---

## Troubleshooting

### Installation Fails at APT Update

```bash
# Manual fix for stubborn cdrom sources
sudo grep -rlnE 'cdrom:|file:/cdrom' /etc/apt/ | \
  sudo xargs -I {} sh -c 'sed -i "s/^/# /" {}'
sudo apt-get update
```

### Docker Still Permission Denied

```bash
# Verify group membership
groups "$USER" | grep docker

# Try manual sg command
sg docker -c "docker ps"

# If still fails, logout and login again
```

### Port Conflict Resolution

```bash
# Find what's using port 18880
lsof -i :18880
ss -tlnp | grep 18880

# Kill the process or use different port
./install_softnix_host_v2.sh --admin-port 19000
```

### Instance Won't Start

```bash
# Check logs
tail -f ~/.softnix/instances/default-prod/logs/gateway.err.log

# Verify Docker image exists
docker images | grep softnixclaw

# Rebuild image if needed
cd /path/to/repo
docker build -t softnixclaw:latest .

# Try start again
~/.softnix/instances/default-prod/scripts/start.sh
```

---

## Migration from v1

If you installed with v1 and encountered issues:

```bash
# 1. Stop existing services
./softnixclaw admin stop

# 2. Fix existing lifecycle scripts
for script in ~/.softnix/instances/*/scripts/*.sh; do
  sudo sed -i '1s|^#!/bin/zsh|#!/usr/bin/env bash|' "$script"
done

# 3. Re-run v2 installer with --force-instance
bash scripts/install_softnix_host_v2.sh \
  --instance-id your-instance-id \
  --force-instance \
  --skip-admin-start \
  -y

# 4. Start Admin
./softnixclaw admin start
```

---

## Testing the Installer

```bash
# Test in clean Ubuntu VM
bash scripts/install_softnix_host_v2.sh \
  --instance-id test-bot \
  --api-key test-key \
  --no-browser \
  -y

# Verify installation
./softnixclaw admin status
docker ps | grep softnix
curl http://127.0.0.1:18880/admin/health
```

---

## Contributing

If you encounter new installation issues, please:

1. Collect logs: `journalctl --user -u softnix-admin > admin.log`
2. Note your Ubuntu version: `lsb_release -a`
3. Open GitHub issue with:
   - Installation command used
   - Full error output
   - System information

---

## See Also

- [README-Softnix-Installation.md](README-Softnix-Installation.md) - Detailed installation guide
- [README-Softnix.md](README-Softnix.md) - Admin usage guide
- [SECURITY.md](SECURITY.md) - Security best practices
