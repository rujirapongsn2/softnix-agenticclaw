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

### 1. Installation

**From Source (Recommended)**
```bash
git clone https://github.com/rujirapongsn2/softnix-agenticclaw.git
cd softnix-agenticclaw
pip install -e .
```

### 2. Initialize Your First Instance
Use `softnix-init` to bootstrap a new managed environment:

```bash
nanobot softnix-init \
  --instance-id production-bot \
  --name "Main Production Assistant" \
  --owner admin \
  --env prod \
  --repo-root $(pwd)
```

### 3. Launch the Admin UI
Start the management server (default port: `18880`):

```bash
nanobot softnix-admin
```
Access the dashboard at [http://127.0.0.1:18880](http://127.0.0.1:18880)

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

-   **Viewer:** Read-only access to dashboard and logs.
-   **Operator:** Can Start/Stop instances and manually trigger scheduled tasks.
-   **Admin:** Full configuration access, memory/skill editing, and user management.
-   **Owner:** Full system control, user deletion, and administrative overrides.

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

For licensing details and enterprise support, please contact **Softnix**.

---
**Maintained by [Softnix](https://www.softnix.co.th)**
