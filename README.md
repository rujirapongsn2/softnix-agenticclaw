# 🛡️ Softnix AgenticClaw

**The Ultimate Control Plane for Enterprise AI Multi-Instance Orchestration.**

Softnix AgenticClaw is an advanced, ultra-lightweight AI control plane designed to manage, monitor, and scale multiple `nanobot` instances. It provides a unified Web-based Admin UI, fine-grained Role-Based Access Control (RBAC), and sophisticated sandboxing strategies to ensure secure and efficient AI operations.

---

## 🔗 Relationship with nanobot

**Softnix AgenticClaw** is built upon the foundation of [nanobot](https://github.com/HKUDS/nanobot). While it leverages the core agent capabilities of nanobot, this project introduces significant enhancements:

1.  **Control Plane Development:** We have developed a comprehensive management layer (Control Plane) that allows orchestration of multiple bots from a single dashboard.
2.  **Core nanobot Enhancements:** We have modified and improved the internal `nanobot` core to support:
    *   **Multi-Instance Registry:** Native support for isolated instance environments.
    *   **Advanced Sandboxing:** Enhanced tool-ephemeral execution and runtime isolation.
    *   **Enterprise Security:** Integrated RBAC and detailed audit logging directly into the agent loop.
    *   **Scalable Architecture:** Optimized for running 100+ concurrent instances with minimal overhead.

---

## 🚀 Key Features

-   **Multi-Instance Management:** Deploy and manage multiple independent bots (tenants, environments, or user groups) from a single interface.
-   **Unified Web Admin UI:** A professional dashboard for real-time monitoring, configuration, and instance control.
-   **Granular RBAC:** 4-level security model (**Viewer → Operator → Admin → Owner**) to strictly control access to logs, configurations, and lifecycle actions.
-   **Advanced Runtime Matrix:** Flexible execution modes including **Persistent Sandbox** and **Tool-Ephemeral Sandbox** for the perfect balance between speed and isolation.
-   **Comprehensive Audit Logging:** Track every activity, runtime event, and security incident with structured audit logs.
-   **Lifecycle Automation:** Standardized scripts (`start.sh`, `stop.sh`, `restart.sh`, `status.sh`) generated automatically for every instance.

---

## 🏗️ Architecture

AgenticClaw follows a clear separation of concerns between the **Control Plane** and the **Instance Plane**:

-   **Control Plane (`~/.softnix/admin`):** Stores the central registry (`instances.json`), audit logs, and global backups.
-   **Instance Plane (`~/.softnix/instances/<id>`):** Each instance has its own isolated directory containing its specific `config.json`, `workspace/`, `logs/`, and process state (`pid`).

---

## 🛠️ Getting Started

### 📋 Prerequisites

- **OS:** Ubuntu 20.04+ (recommended) or any Linux distribution
- **Python:** 3.11 or higher
- **Git:** For cloning the repository
- **Docker:** Required for sandbox mode (Docker Engine 20.10+)
- **uv:** Python package manager (will be installed automatically)

---

### 🚀 Quick Installation (Recommended)

**One-Command Installer (v2)**

The easiest way to install Softnix AgenticClaw with all dependencies:

```bash
# Clone the repository
git clone https://github.com/rujirapongsn2/softnix-agenticclaw.git
cd softnix-agenticclaw

# Run the interactive installer
bash scripts/install_softnix_host_v2.sh
```

**Installer Options:**

```bash
# Non-interactive mode (auto-yes to all prompts)
bash scripts/install_softnix_host_v2.sh --yes

# With WhatsApp Bridge and Playwright support
bash scripts/install_softnix_host_v2.sh --with-whatsapp --with-playwright-deps

# Specify instance configuration
bash scripts/install_softnix_host_v2.sh \
  --instance-id my-prod \
  --instance-name "My Production Bot" \
  --admin-port 18880

# With provider API key (for immediate use)
bash scripts/install_softnix_host_v2.sh \
  --provider openrouter \
  --model openai/gpt-4o \
  --api-key "sk-or-your-api-key-here"
```

**What the installer does:**
1. ✅ Checks system requirements (Ubuntu, Python, Docker)
2. ✅ Installs missing dependencies (Python, uv, Docker Engine)
3. ✅ Creates virtual environment (`.venv`) and installs `nanobot`
4. ✅ Builds sandbox Docker image (`softnixclaw:latest`)
5. ✅ Bootstraps your first instance
6. ✅ Starts the Admin UI service
7. ✅ Opens the dashboard in your browser

---

### 🔧 Manual Installation (Advanced)

**Step 1: Clone and Setup Environment**

```bash
# Clone the repository
git clone https://github.com/rujirapongsn2/softnix-agenticclaw.git
cd softnix-agenticclaw

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment and install dependencies
uv venv
source .venv/activate
uv pip install -e .
```

**Step 2: Install Docker (if not installed)**

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Logout and login again for group changes to take effect
```

**Step 3: Build Sandbox Image**

```bash
docker build -t softnixclaw:latest .
```

**Step 4: Initialize Your First Instance**

Use `softnix-init` to bootstrap a new managed environment:

```bash
nanobot softnix-init \
  --instance-id production-bot \
  --name "Main Production Assistant" \
  --owner admin \
  --env prod \
  --repo-root $(pwd)
```

---

### 🎛️ Service Management CLI (`softnixclaw`)

For production and ease of use, we provide a unified management script named `softnixclaw` at the project root.

#### **A. Installation (Linux Auto-Start)**
If you are on Linux, you can install AgenticClaw as a **Systemd User Service** so it starts automatically on boot and persists after logout:
```bash
chmod +x softnixclaw
./softnixclaw install
```
*This command enables auto-start and lingering for the current user.*

#### **B. Admin Service Control**
Manage the Admin UI service (whether running via Systemd or background process):
```bash
./softnixclaw admin start    # Start the admin server
./softnixclaw admin stop     # Stop the admin server
./softnixclaw admin restart  # Restart the admin server
./softnixclaw admin status   # Check status and recent logs
```

**Access the dashboard:**
- Local: [http://127.0.0.1:18880](http://127.0.0.1:18880)
- Remote: `http://<your-server-ip>:18880`

> **Note:** Default port is **18880**

#### **C. Project Update**
Keep your installation up-to-date with a single command:
```bash
./softnixclaw update
```
*This performs a `git pull`, updates dependencies, restarts `Softnix Admin`, and then decides whether sandbox images or running instances must be refreshed based on the files changed in the new commit range.*

Update behavior is intentionally automatic:
- UI / docs / static asset changes: restart `Softnix Admin` only
- Runtime, dependency, or Docker changes: rebuild sandbox images and restart running instances that use the updated runtime
- Instance lifecycle changes: restart only the instances that are currently running

If the repository is not a git checkout, `./softnixclaw update` skips `git pull` and only applies the local service restart flow.

---

### ⚙️ Instance Management

**Start an Instance:**
```bash
# Using the instance's start script
~/.softnix/instances/<instance-id>/scripts/start.sh

# Or via Admin UI
# Navigate to Instances → Select instance → Click "Start"
```

**Stop an Instance:**
```bash
~/.softnix/instances/<instance-id>/scripts/stop.sh
```

**Check Instance Status:**
```bash
~/.softnix/instances/<instance-id>/scripts/status.sh
```

**Configure an Instance:**
```bash
# Edit config directly
nano ~/.softnix/instances/<instance-id>/config.json

# Or via Admin UI
# Navigate to Instances → Select instance → Configuration tab
```

> **Important:** Before starting an instance, ensure you have configured:
> - **Provider** (e.g., `openrouter`, `openai`, `anthropic`)
> - **Model** (e.g., `openai/gpt-4o`, `anthropic/claude-sonnet-4-5`)
> - **API Key** (required for LLM access)

---

## ⚙️ Runtime & Sandboxing Strategy

AgenticClaw supports multiple profiles to match your security and performance needs:

| Profile | Strategy | Best For |
| :--- | :--- | :--- |
| **Strict** | Sandbox + Persistent | Maximum isolation, offline workloads, high security. |
| **Balanced** | Sandbox + Persistent | Standard production tasks (Telegram, MCP, External APIs). |
| **Fast** | Host + Tool-Ephemeral | Maximum performance; spawns sandboxes only for risky tool calls. |

---

## 🔐 Security & RBAC

AgenticClaw implements a strict permission matrix:

-   **Viewer:** Read-only access to the dashboard, logs, and assigned instances.
-   **Operator:** Can control assigned instances and manually trigger schedules.
-   **Admin:** Can manage configuration, content, and users within assigned instance scope. Admin cannot create or delete instances.
-   **Owner System:** Full system control, including instance creation/deletion, security policy changes, and owner-level user management.

Instance visibility is scoped by `instance_ids` on the user record. Non-owner users only see and manage the instances assigned to them. Security Audit Log is also scoped to the current user by default, with filters for own events, assigned instances, and full-access views for Owner System accounts.

---

## 📁 Project Structure

```text
/
├── nanobot/
│   ├── admin/       # Softnix Admin UI (FastAPI + Tailwind)
│   ├── agent/       # Core Agent Logic (Loop, Tools, Memory)
│   ├── runtime/     # Sandboxing & Ephemeral Execution logic
│   ├── channels/    # Integrations (Telegram, Discord, Feishu, etc.)
│   └── ...          # Providers, Bus, Sessions
├── bridge/          # External microservices (e.g., WhatsApp Bridge)
├── scripts/         # Host environment setup scripts
└── tests/           # Robust test suite for all modules
```

---

## 📄 Credits & License

Softnix AgenticClaw is an extended version of the original [nanobot](https://github.com/HKUDS/nanobot) project. 

- **nanobot:** Licensed under its original [MIT License](https://github.com/HKUDS/nanobot/blob/main/LICENSE).
- **Softnix AgenticClaw:** All enhancements, the control plane, and administrative tools are also licensed under the **MIT License**.

For enterprise support and inquiries, please contact **Softnix**.

---
**Maintained by [Softnix](https://www.softnix.co.th)**
