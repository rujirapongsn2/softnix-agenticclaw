const state = {
  overview: null,
  channels: [],
  accessRequests: { requests: [], count: 0 },
  security: null,
  activity: null,
  auth: {
    initialized: false,
    authenticated: false,
    bootstrapRequired: false,
    user: null,
    session: null,
    users: [],
  },
  overviewRuntimeAuditByInstance: {},
  schedules: null,
  instanceEditor: null,
  instanceConfig: {
    instanceId: "",
    text: "",
    dirty: false,
    loading: false,
  },
  selectedInstanceId: "",
  deleteCandidateId: "",
  channelFocusByInstance: {},
  providerModeByInstance: {},
  providerFocusByInstance: {},
  mcpFocusByInstance: {},
  mcpCreateOpenByInstance: {},
  memoryByInstance: {},
  memoryViewModeByInstance: {},
  skillsByInstance: {},
  instanceWorkspaceTab: "manage",
  currentView: "overview",
  busyKey: "",
  liveInstanceFilter: "all",
  overviewOpsInstanceId: "all",
  liveExpandedEvents: {},
  instanceCreateOpen: false,
  runtimeAudit: {
    instanceId: "",
    status: "all",
    operation: "all",
    search: "",
    events: [],
    summary: null,
    filteredCount: 0,
    nextCursor: null,
    initialized: false,
    loading: false,
    loadingMore: false,
    autoRefresh: true,
    debounceId: null,
    executionMode: "live",
    executionPinnedTraceId: "",
    executionLatestTraceId: "",
    executionUnreadTraceCount: 0,
    executionZoom: 1,
    executionFitToWindow: false,
  },
  userModal: { open: false, userId: null },
  auditLog: {
    events: [],
    total: 0,
    offset: 0,
    limit: 100,
    category: "all",
    outcome: "all",
    search: "",
    loading: false,
    initialized: false,
    debounceId: null,
  },
};

const EXECUTION_ZOOM_MIN = 0.4;
const EXECUTION_ZOOM_MAX = 3.0;
const EXECUTION_ZOOM_STEP = 1.2;

function clampExecutionZoom(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return 1;
  return Math.max(EXECUTION_ZOOM_MIN, Math.min(EXECUTION_ZOOM_MAX, num));
}

const SANDBOX_PROFILE_DEFAULTS = {
  strict: {
    runtimeMode: "sandbox",
    sandboxExecutionStrategy: "persistent",
    sandboxCpuLimit: "1.0",
    sandboxMemoryLimit: "1g",
    sandboxPidsLimit: "128",
    sandboxTmpfsSizeMb: "64",
    sandboxNetworkPolicy: "none",
    sandboxTimeoutSeconds: "60",
  },
  balanced: {
    runtimeMode: "sandbox",
    sandboxExecutionStrategy: "persistent",
    sandboxCpuLimit: "2.0",
    sandboxMemoryLimit: "2g",
    sandboxPidsLimit: "256",
    sandboxTmpfsSizeMb: "128",
    sandboxNetworkPolicy: "default",
    sandboxTimeoutSeconds: "90",
  },
  fast: {
    runtimeMode: "host",
    sandboxExecutionStrategy: "tool_ephemeral",
    sandboxCpuLimit: "",
    sandboxMemoryLimit: "",
    sandboxPidsLimit: "512",
    sandboxTmpfsSizeMb: "256",
    sandboxNetworkPolicy: "default",
    sandboxTimeoutSeconds: "180",
  },
};

function normalizeSandboxProfile(value) {
  return SANDBOX_PROFILE_DEFAULTS[value] ? value : "balanced";
}

function applySandboxProfileToEditor(editor, profile) {
  const normalized = normalizeSandboxProfile(profile);
  const defaults = SANDBOX_PROFILE_DEFAULTS[normalized];
  return {
    ...editor,
    sandboxProfile: normalized,
    runtimeMode: defaults.runtimeMode,
    sandboxExecutionStrategy: defaults.sandboxExecutionStrategy,
    sandboxCpuLimit: defaults.sandboxCpuLimit,
    sandboxMemoryLimit: defaults.sandboxMemoryLimit,
    sandboxPidsLimit: defaults.sandboxPidsLimit,
    sandboxTmpfsSizeMb: defaults.sandboxTmpfsSizeMb,
    sandboxNetworkPolicy: defaults.sandboxNetworkPolicy,
    sandboxTimeoutSeconds: defaults.sandboxTimeoutSeconds,
  };
}

function applySandboxProfileToRuntimeControls(instanceId, profile) {
  const normalized = normalizeSandboxProfile(profile);
  const defaults = SANDBOX_PROFILE_DEFAULTS[normalized];
  const modeControl = document.querySelector(`[data-runtime-mode="${CSS.escape(instanceId)}"]`);
  const strategyControl = document.querySelector(`[data-runtime-strategy="${CSS.escape(instanceId)}"]`);
  const networkControl = document.querySelector(`[data-runtime-network="${CSS.escape(instanceId)}"]`);
  if (modeControl) modeControl.value = defaults.runtimeMode;
  if (strategyControl) strategyControl.value = defaults.sandboxExecutionStrategy;
  if (networkControl) {
    networkControl.value = defaults.sandboxNetworkPolicy;
    networkControl.disabled = !(defaults.runtimeMode === "sandbox" || defaults.sandboxExecutionStrategy === "tool_ephemeral");
  }
}

function sandboxProfileLabel(profile) {
  const normalized = normalizeSandboxProfile(profile);
  if (normalized === "strict") return "Strict";
  if (normalized === "fast") return "Max Capability";
  return "Connected";
}

function sandboxProfileSummary(profile) {
  const normalized = normalizeSandboxProfile(profile);
  if (normalized === "strict") return "Offline-first isolation with outbound network disabled by default.";
  if (normalized === "fast") return "Host control plane with ephemeral sandbox only when a task needs tools.";
  return "Connected sandbox for Telegram, MCP, and provider APIs.";
}

function isCustomRuntimeConfig(editor) {
  const defaults = SANDBOX_PROFILE_DEFAULTS[normalizeSandboxProfile(editor.sandboxProfile || "balanced")];
  return (
    (editor.runtimeMode || "") !== defaults.runtimeMode ||
    (editor.sandboxExecutionStrategy || "") !== defaults.sandboxExecutionStrategy ||
    (editor.sandboxCpuLimit || "") !== defaults.sandboxCpuLimit ||
    (editor.sandboxMemoryLimit || "") !== defaults.sandboxMemoryLimit ||
    String(editor.sandboxPidsLimit || "") !== defaults.sandboxPidsLimit ||
    String(editor.sandboxTmpfsSizeMb || "") !== defaults.sandboxTmpfsSizeMb ||
    (editor.sandboxNetworkPolicy || "") !== defaults.sandboxNetworkPolicy ||
    String(editor.sandboxTimeoutSeconds || "") !== defaults.sandboxTimeoutSeconds
  );
}

function runtimeImpactSummary(editor) {
  const runtimeMode = editor.runtimeMode || "sandbox";
  const strategy = editor.sandboxExecutionStrategy || "persistent";
  const network = editor.sandboxNetworkPolicy || "default";
  const internet = network === "none" ? "Blocked" : "Enabled";
  const toolExecution =
    runtimeMode === "host" && strategy === "tool_ephemeral"
      ? "General chat inline, tool tasks run in ephemeral sandbox"
      : runtimeMode === "sandbox"
        ? "Gateway stays inside sandbox continuously"
        : "Gateway and tools run on host";
  return { internet, toolExecution };
}

const views = {
  overview: document.getElementById("overview-view"),
  instances: document.getElementById("instances-view"),
  live: document.getElementById("live-view"),
  schedules: document.getElementById("schedules-view"),
  providers: document.getElementById("providers-view"),
  channels: document.getElementById("channels-view"),
  security: document.getElementById("security-view"),
  users: document.getElementById("users-view"),
};
const ALLOWED_VIEWS = new Set(Object.keys(views));
const ALLOWED_WORKSPACE_TABS = new Set(["manage", "channels", "providers", "memory", "skills", "schedules", "security", "runtime-audit", "execution-visualize"]);

const VIEW_PERMISSIONS = {
  overview: "overview.read",
  instances: "instance.read",
  live: "activity.read",
  schedules: "schedule.read",
  providers: "provider.read",
  channels: "channel.read",
  security: "security.read",
  users: "user.read",
};

function safeView(value) {
  return ALLOWED_VIEWS.has(value) ? value : "overview";
}

function authPermissions() {
  return new Set(state.auth.user?.permissions || []);
}

function hasAuthPermission(permission) {
  if (!permission) return true;
  return authPermissions().has(permission);
}

function firstAllowedView() {
  const order = ["overview", "instances", "live", "security", "users"];
  return order.find((view) => hasAuthPermission(VIEW_PERMISSIONS[view])) || "overview";
}

function safeAuthorizedView(value) {
  const next = safeView(value);
  return hasAuthPermission(VIEW_PERMISSIONS[next]) ? next : firstAllowedView();
}

function safeWorkspaceTab(value) {
  return ALLOWED_WORKSPACE_TABS.has(value) ? value : "manage";
}

function defaultSkillState() {
  return {
    loading: false,
    loadingFiles: false,
    skills: [],
    selectedSkill: null,
    files: {},
    selectedFile: null,
  };
}

function getSkillState(instanceId) {
  if (!state.skillsByInstance[instanceId]) {
    state.skillsByInstance[instanceId] = defaultSkillState();
  }
  return state.skillsByInstance[instanceId];
}

function defaultMemoryState() {
  return {
    loading: false,
    selectedPath: "AGENTS.md",
    files: {},
  };
}

function getMemoryState(instanceId) {
  if (!state.memoryByInstance[instanceId]) {
    state.memoryByInstance[instanceId] = defaultMemoryState();
  }
  return state.memoryByInstance[instanceId];
}

function syncLocationState() {
  const url = new URL(window.location.href);
  url.searchParams.set("view", safeView(state.currentView));
  if (state.selectedInstanceId) {
    url.searchParams.set("instance", state.selectedInstanceId);
  } else {
    url.searchParams.delete("instance");
  }
  if (state.currentView === "instances") {
    url.searchParams.set("tab", safeWorkspaceTab(state.instanceWorkspaceTab));
  } else {
    url.searchParams.delete("tab");
  }
  if (state.currentView === "live" && state.liveInstanceFilter !== "all") {
    url.searchParams.set("live_instance", state.liveInstanceFilter);
  } else {
    url.searchParams.delete("live_instance");
  }
  const next = `${url.pathname}${url.search}`;
  window.history.replaceState({}, "", next);
}

function restoreLocationState() {
  const params = new URLSearchParams(window.location.search);
  const view = params.get("view");
  const safeCurrentView = safeView(view || state.currentView);
  const instanceId = params.get("instance");
  const tab = params.get("tab");
  const liveInstance = params.get("live_instance");
  if (view) {
    state.currentView = safeCurrentView;
  }
  if (instanceId) {
    state.selectedInstanceId = instanceId;
  }
  if (tab) {
    state.instanceWorkspaceTab = safeWorkspaceTab(tab);
  }
  if (liveInstance) {
    state.liveInstanceFilter = liveInstance;
  } else if (safeCurrentView === "live" && instanceId) {
    state.liveInstanceFilter = instanceId;
  }
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value || 0);
}

function formatDateTime(value) {
  if (!value) return "No recent activity";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function parseTimestamp(value) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const parsed = Date.parse(raw);
  if (Number.isFinite(parsed)) return parsed;
  const normalized = raw.replace(/(\.\d{3})\d+/, "$1");
  const fallback = Date.parse(normalized);
  return Number.isFinite(fallback) ? fallback : null;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function badgeClass(severity) {
  if (severity === "warning") return "is-orange";
  if (severity === "info") return "is-blue";
  if (severity === "ok") return "is-lime";
  if (severity === "error") return "is-red";
  return "is-gray";
}

async function fetchJson(path) {
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    headers: {
      "Cache-Control": "no-cache",
      Pragma: "no-cache",
    },
  });
  if (response.status === 401) {
    state.auth.authenticated = false;
    state.auth.user = null;
    state.auth.session = null;
    renderAuthShell();
    throw new Error("Authentication required");
  }
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

async function patchJson(path, payload) {
  const response = await fetch(path, {
    method: "PATCH",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": state.auth.session?.csrf_token || "",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": state.auth.session?.csrf_token || "",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

async function deleteJson(path, payload) {
  const response = await fetch(path, {
    method: "DELETE",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": state.auth.session?.csrf_token || "",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

function summarizeCommandResult(result) {
  const detail = result.stderr || result.stdout || "Command completed.";
  return `Instance '${result.instance.name}' ${result.action}: ${result.ok ? "ok" : "failed"}. ${detail}`;
}

function setBanner(message, type = "warning") {
  const banner = document.getElementById("alert-banner");
  banner.textContent = message;
  banner.classList.remove("is-hidden");
  banner.style.borderColor = type === "error" ? "rgba(199,81,70,0.3)" : "rgba(243,144,63,0.3)";
  banner.style.background = type === "error" ? "rgba(199,81,70,0.08)" : "rgba(243,144,63,0.1)";
  banner.style.color = type === "error" ? "#9a3e36" : "#8a4b16";
}

function clearBanner() {
  document.getElementById("alert-banner").classList.add("is-hidden");
}

function currentUserRole() {
  return state.auth.user?.role || "viewer";
}

function roleLabel(role) {
  if (role === "owner") return "Owner";
  if (role === "admin") return "Admin";
  if (role === "operator") return "Operator";
  return "Viewer";
}

function authDisplayName(user) {
  if (!user) return "";
  return user.display_name || user.username || "Admin User";
}

function renderNavigation() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    const permission = VIEW_PERMISSIONS[button.dataset.view];
    const visible = state.auth.authenticated && hasAuthPermission(permission);
    button.classList.toggle("is-hidden", !visible);
  });
}

function renderUserMenu() {
  const target = document.getElementById("auth-user-menu");
  if (!target) return;
  if (!state.auth.authenticated || !state.auth.user) {
    target.innerHTML = "";
    return;
  }
  target.innerHTML = `
    <div class="inline-actions user-menu">
      <div class="user-menu-copy">
        <strong>${escapeHtml(authDisplayName(state.auth.user))}</strong>
        <span class="badge ${badgeClass("info")}">${escapeHtml(roleLabel(currentUserRole()))}</span>
      </div>
      <button id="logout-button" class="secondary-button">Logout</button>
    </div>
  `;
  document.getElementById("logout-button")?.addEventListener("click", () => {
    void handleLogout();
  });
}

function renderAuthPanel() {
  const target = document.getElementById("auth-panel");
  if (!target) return;
  if (state.auth.bootstrapRequired) {
    target.innerHTML = `
      <div class="stack">
        <div>
          <p class="eyebrow">First-time Setup</p>
          <h2>Bootstrap owner account</h2>
          <p class="meta">Create the first owner for this Softnix Admin Console.</p>
        </div>
        <div class="field">
          <label for="bootstrap-display-name">Display name</label>
          <input id="bootstrap-display-name" type="text" placeholder="Team Owner">
        </div>
        <div class="field">
          <label for="bootstrap-username">Username</label>
          <input id="bootstrap-username" type="text" placeholder="owner">
        </div>
        <div class="field">
          <label for="bootstrap-email">Email</label>
          <input id="bootstrap-email" type="email" placeholder="owner@example.com">
        </div>
        <div class="field">
          <label for="bootstrap-password">Password</label>
          <input id="bootstrap-password" type="password" placeholder="At least 8 characters">
        </div>
        <div class="inline-actions">
          <button id="bootstrap-submit" class="primary-button">Create Owner Account</button>
        </div>
      </div>
    `;
    document.getElementById("bootstrap-submit")?.addEventListener("click", () => {
      void handleBootstrapSubmit();
    });
    return;
  }
  target.innerHTML = `
    <div class="stack">
      <div>
        <p class="eyebrow">Authentication</p>
        <h2>Sign in to Softnix Admin</h2>
        <p class="meta">Use your username or email and password.</p>
      </div>
      <div class="field">
        <label for="login-name">Username or email</label>
        <input id="login-name" type="text" autocomplete="username" placeholder="owner">
      </div>
      <div class="field">
        <label for="login-password">Password</label>
        <input id="login-password" type="password" autocomplete="current-password" placeholder="Password">
      </div>
      <div class="inline-actions">
        <button id="login-submit" class="primary-button">Login</button>
      </div>
    </div>
  `;
  document.getElementById("login-submit")?.addEventListener("click", () => {
    void handleLoginSubmit();
  });
}

function renderUsersPanel() {
  const target = document.getElementById("users-panel");
  if (!target) return;
  if (!hasAuthPermission("user.read")) {
    target.innerHTML = `<div class="users-empty"><p class="meta">You do not have permission to view users.</p></div>`;
    return;
  }
  const canCreate = hasAuthPermission("user.create");
  const canUpdate = hasAuthPermission("user.update");
  const users = state.auth.users;

  const header = document.getElementById("users-panel-header");
  if (header) {
    const existing = header.querySelector(".users-add-btn-wrap");
    if (existing) existing.remove();
    if (canCreate) {
      const wrap = document.createElement("div");
      wrap.className = "users-add-btn-wrap";
      wrap.innerHTML = `<button class="primary-button is-small" id="add-user-btn">+ Add User</button>`;
      header.appendChild(wrap);
      wrap.querySelector("#add-user-btn")?.addEventListener("click", () => openUserModal(null));
    }
  }

  if (users.length === 0) {
    target.innerHTML = `<div class="users-empty"><p class="meta">No team members yet.${canCreate ? " Click <strong>+ Add User</strong> to get started." : ""}</p></div>`;
    return;
  }

  const roleCls = { owner: "role-owner", admin: "role-admin", operator: "role-operator", viewer: "role-viewer" };
  const avatarBg = {
    owner:    ["#1d4ed8", "#dbeafe"],
    admin:    ["#7e22ce", "#f3e8ff"],
    operator: ["#0369a1", "#e0f2fe"],
    viewer:   ["#475569", "#f1f5f9"],
  };

  const avatarHtml = (user) => {
    const initial = (user.display_name || user.username || "?").charAt(0).toUpperCase();
    const [fg, bg] = avatarBg[user.role] || avatarBg.viewer;
    return `<div class="user-avatar" style="background:${bg};color:${fg}">${escapeHtml(initial)}</div>`;
  };
  const roleBadge = (role) =>
    `<span class="role-badge ${roleCls[role] || "role-viewer"}">${escapeHtml(roleLabel(role))}</span>`;
  const statusBadge = (status) =>
    `<span class="role-badge ${status === "disabled" ? "status-disabled" : "status-active"}">${status === "disabled" ? "Disabled" : "Active"}</span>`;
  const fmtLastLogin = (ts) => {
    if (!ts) return `<span style="color:var(--fg-secondary);font-size:13px">—</span>`;
    try {
      const diff = Date.now() - new Date(ts).getTime();
      const days = Math.floor(diff / 86400000);
      if (days === 0) return "Today";
      if (days === 1) return "Yesterday";
      if (days < 7) return `${days}d ago`;
      return new Date(ts).toLocaleDateString();
    } catch { return `<span style="color:var(--fg-secondary);font-size:13px">—</span>`; }
  };

  const roleCounts = {};
  users.forEach((u) => { roleCounts[u.role] = (roleCounts[u.role] || 0) + 1; });
  const summaryHtml = Object.entries(roleCounts)
    .map(([r, n]) => `<span class="users-summary-stat"><strong>${n}</strong>&nbsp;${escapeHtml(roleLabel(r))}</span>`)
    .join("") + `<span class="users-summary-stat" style="margin-left:auto"><strong>${users.length}</strong>&nbsp;total</span>`;

  target.innerHTML = `
    <div class="users-summary">${summaryHtml}</div>
    <div class="users-table-wrap">
      <table class="users-table">
        <thead>
          <tr>
            <th>User</th>
            <th>Role</th>
            <th>Status</th>
            <th>Last Login</th>
            ${canUpdate ? "<th></th>" : ""}
          </tr>
        </thead>
        <tbody>
          ${users.map((user) => `
            <tr>
              <td>
                <div class="user-cell">
                  ${avatarHtml(user)}
                  <div>
                    <div class="user-cell-name">${escapeHtml(authDisplayName(user))}</div>
                    <div class="user-cell-meta">${escapeHtml(user.username)}${user.email ? ` · ${escapeHtml(user.email)}` : ""}</div>
                  </div>
                </div>
              </td>
              <td>${roleBadge(user.role)}</td>
              <td>${statusBadge(user.status)}</td>
              <td style="font-size:13px;color:var(--fg-secondary)">${fmtLastLogin(user.last_login_at)}</td>
              ${canUpdate ? `<td><button class="secondary-button is-small" data-user-edit="${escapeHtml(user.id)}">Edit</button></td>` : ""}
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;

  target.querySelectorAll("[data-user-edit]").forEach((btn) => {
    btn.addEventListener("click", () => openUserModal(btn.dataset.userEdit));
  });
}

function renderAccountPanel() {
  const target = document.getElementById("account-panel");
  if (!target) return;
  if (!state.auth.user) { target.innerHTML = ""; return; }
  const user = state.auth.user;
  const avatarBg = {
    owner:    ["#1d4ed8", "#dbeafe"],
    admin:    ["#7e22ce", "#f3e8ff"],
    operator: ["#0369a1", "#e0f2fe"],
    viewer:   ["#475569", "#f1f5f9"],
  };
  const [fg, bg] = avatarBg[user.role] || avatarBg.viewer;
  const initials = (user.display_name || user.username || "?").substring(0, 2).toUpperCase();
  const roleCls = { owner: "role-owner", admin: "role-admin", operator: "role-operator", viewer: "role-viewer" };
  target.innerHTML = `
    <div class="account-card">
      <div class="account-profile">
        <div class="user-avatar user-avatar-lg" style="background:${bg};color:${fg}">${escapeHtml(initials)}</div>
        <div>
          <div class="user-cell-name" style="font-size:16px">${escapeHtml(authDisplayName(user))}</div>
          <div class="user-cell-meta" style="margin-top:5px">
            ${escapeHtml(user.username)}
            &nbsp;·&nbsp;
            <span class="role-badge ${roleCls[user.role] || "role-viewer"}">${escapeHtml(roleLabel(user.role))}</span>
          </div>
        </div>
      </div>
      <div class="account-divider"></div>
      <div class="account-password-section">
        <h4>Change Password</h4>
        <div class="stack">
          <div class="field">
            <label for="current-password">Current password</label>
            <input id="current-password" type="password" autocomplete="current-password">
          </div>
          <div class="field">
            <label for="new-password">New password</label>
            <input id="new-password" type="password" autocomplete="new-password">
          </div>
          <div class="inline-actions">
            <button id="change-password-button" class="primary-button">Update Password</button>
          </div>
        </div>
      </div>
    </div>
  `;
  document.getElementById("change-password-button")?.addEventListener("click", () => {
    void handleChangePassword();
  });
}

function openUserModal(userId) {
  state.userModal = { open: true, userId: userId || null };
  const modal = document.getElementById("user-modal");
  if (modal) {
    modal.classList.remove("is-hidden");
    modal.onclick = (e) => { if (e.target === modal) closeUserModal(); };
  }
  const closeBtn = document.getElementById("user-modal-close");
  if (closeBtn) closeBtn.onclick = closeUserModal;
  renderUserModal();
}

function closeUserModal() {
  state.userModal = { open: false, userId: null };
  document.getElementById("user-modal")?.classList.add("is-hidden");
}

function renderUserModal() {
  const titleEl = document.getElementById("user-modal-title");
  const body = document.getElementById("user-modal-body");
  if (!titleEl || !body) return;
  const userId = state.userModal.userId;
  const isEdit = !!userId;
  const user = isEdit ? state.auth.users.find((u) => u.id === userId) : null;
  const canEditRole = !isEdit || currentUserRole() === "owner" || !!(user && user.role !== "owner");
  const canDisable = hasAuthPermission("user.disable");
  titleEl.textContent = isEdit ? `Edit — ${user ? authDisplayName(user) : "User"}` : "Add Team Member";
  body.innerHTML = `
    <div class="stack">
      ${!isEdit ? `
      <div class="field">
        <label for="modal-user-username">Username <span style="color:var(--status-danger)">*</span></label>
        <input id="modal-user-username" type="text" placeholder="jane" autocomplete="off">
      </div>` : ""}
      <div class="field">
        <label for="modal-user-display-name">Display name</label>
        <input id="modal-user-display-name" type="text" placeholder="Jane Doe"
          value="${isEdit && user ? escapeHtml(user.display_name || "") : ""}">
      </div>
      <div class="field">
        <label for="modal-user-email">Email</label>
        <input id="modal-user-email" type="email" placeholder="jane@example.com"
          value="${isEdit && user ? escapeHtml(user.email || "") : ""}">
      </div>
      <div class="field">
        <label for="modal-user-role">Role</label>
        <select id="modal-user-role" ${!canEditRole ? "disabled" : ""}>
          <option value="viewer"   ${isEdit && user?.role === "viewer"   ? "selected" : ""}>Viewer</option>
          <option value="operator" ${isEdit && user?.role === "operator" ? "selected" : ""}>Operator</option>
          <option value="admin"    ${isEdit && user?.role === "admin"    ? "selected" : ""}>Admin</option>
          ${currentUserRole() === "owner" ? `<option value="owner" ${isEdit && user?.role === "owner" ? "selected" : ""}>Owner</option>` : ""}
        </select>
      </div>
      ${isEdit ? `
      <div class="field">
        <label for="modal-user-status">Status</label>
        <select id="modal-user-status" ${!canDisable ? "disabled" : ""}>
          <option value="active"   ${!user || user.status !== "disabled" ? "selected" : ""}>Active</option>
          ${canDisable ? `<option value="disabled" ${user?.status === "disabled" ? "selected" : ""}>Disabled</option>` : ""}
        </select>
      </div>` : `
      <div class="field">
        <label for="modal-user-password">Temporary password <span style="color:var(--status-danger)">*</span></label>
        <input id="modal-user-password" type="password" placeholder="At least 8 characters" autocomplete="new-password">
      </div>`}
      <div class="modal-footer-actions">
        ${isEdit ? `<button class="secondary-button is-small" id="modal-reset-pw-btn">Reset Password</button>` : ""}
        <div style="flex:1"></div>
        <button class="secondary-button" id="modal-cancel-btn">Cancel</button>
        <button class="primary-button" id="modal-confirm-btn">${isEdit ? "Save Changes" : "Create User"}</button>
      </div>
    </div>
  `;
  document.getElementById("modal-cancel-btn")?.addEventListener("click", closeUserModal);
  document.getElementById("modal-confirm-btn")?.addEventListener("click", () => {
    if (isEdit) { void handleSaveUser(userId); } else { void handleCreateUser(); }
  });
  document.getElementById("modal-reset-pw-btn")?.addEventListener("click", () => {
    void handleResetUserPassword(userId);
  });
}

function renderAuthShell() {
  const authScreen = document.getElementById("auth-screen");
  const appShell = document.getElementById("app-shell");
  if (!authScreen || !appShell) return;
  authScreen.classList.toggle("is-hidden", state.auth.authenticated);
  appShell.classList.toggle("is-hidden", !state.auth.authenticated);
  renderNavigation();
  renderUserMenu();
  if (!state.auth.authenticated) {
    renderAuthPanel();
    return;
  }
  state.currentView = safeAuthorizedView(state.currentView);
  renderUsersPanel();
  renderAccountPanel();
}

async function loadAuthState() {
  const response = await fetch("/admin/auth/me", {
    cache: "no-store",
    credentials: "same-origin",
    headers: {
      "Cache-Control": "no-cache",
      Pragma: "no-cache",
    },
  });
  const data = await response.json();
  state.auth.initialized = true;
  state.auth.authenticated = !!data.authenticated;
  state.auth.bootstrapRequired = !!data.bootstrap_required;
  state.auth.user = data.user || null;
  state.auth.session = data.session || null;
  if (!state.auth.authenticated) {
    state.auth.users = [];
  } else if (hasAuthPermission("user.read")) {
    try {
      const users = await fetchJson("/admin/users");
      state.auth.users = users.users || [];
    } catch (_error) {
      state.auth.users = [];
    }
  } else {
    state.auth.users = [];
  }
  renderAuthShell();
}

async function handleBootstrapSubmit() {
  try {
    const payload = {
      display_name: document.getElementById("bootstrap-display-name")?.value || "",
      username: document.getElementById("bootstrap-username")?.value || "",
      email: document.getElementById("bootstrap-email")?.value || "",
      password: document.getElementById("bootstrap-password")?.value || "",
    };
    const result = await postJson("/admin/auth/bootstrap", payload);
    state.auth.authenticated = !!result.authenticated;
    state.auth.bootstrapRequired = false;
    state.auth.user = result.user || null;
    state.auth.session = result.session || null;
    clearBanner();
    await loadAuthState();
    await loadDashboard();
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function handleLoginSubmit() {
  try {
    const payload = {
      login: document.getElementById("login-name")?.value || "",
      password: document.getElementById("login-password")?.value || "",
    };
    const result = await postJson("/admin/auth/login", payload);
    state.auth.authenticated = !!result.authenticated;
    state.auth.user = result.user || null;
    state.auth.session = result.session || null;
    clearBanner();
    await loadAuthState();
    await loadDashboard();
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function handleLogout() {
  try {
    await postJson("/admin/auth/logout", {});
  } catch (_error) {
  }
  state.auth.authenticated = false;
  state.auth.user = null;
  state.auth.session = null;
  state.auth.users = [];
  renderAuthShell();
}

async function handleCreateUser() {
  try {
    await postJson("/admin/users", {
      display_name: document.getElementById("modal-user-display-name")?.value || "",
      username: document.getElementById("modal-user-username")?.value || "",
      email: document.getElementById("modal-user-email")?.value || "",
      role: document.getElementById("modal-user-role")?.value || "viewer",
      password: document.getElementById("modal-user-password")?.value || "",
    });
    clearBanner();
    closeUserModal();
    await loadAuthState();
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function handleSaveUser(userId) {
  try {
    await patchJson(`/admin/users/${encodeURIComponent(userId)}`, {
      display_name: document.getElementById("modal-user-display-name")?.value || "",
      email: document.getElementById("modal-user-email")?.value || "",
      role: document.getElementById("modal-user-role")?.value || "viewer",
      status: document.getElementById("modal-user-status")?.value || "active",
    });
    clearBanner();
    closeUserModal();
    await loadAuthState();
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function handleResetUserPassword(userId) {
  const titleEl = document.getElementById("user-modal-title");
  const body = document.getElementById("user-modal-body");
  if (!body) return;
  if (titleEl) titleEl.textContent = "Reset Password";
  body.innerHTML = `
    <div class="stack">
      <p class="meta">Set a new temporary password. The user should change it after their next login.</p>
      <div class="field">
        <label for="modal-reset-new-pw">New temporary password</label>
        <input id="modal-reset-new-pw" type="password" placeholder="At least 8 characters" autocomplete="new-password">
      </div>
      <div class="modal-footer-actions">
        <button class="secondary-button is-small" id="modal-reset-back-btn">← Back</button>
        <div style="flex:1"></div>
        <button class="secondary-button" id="modal-reset-cancel-btn">Cancel</button>
        <button class="primary-button" id="modal-reset-confirm-btn">Reset Password</button>
      </div>
    </div>
  `;
  document.getElementById("modal-reset-back-btn")?.addEventListener("click", renderUserModal);
  document.getElementById("modal-reset-cancel-btn")?.addEventListener("click", closeUserModal);
  document.getElementById("modal-reset-confirm-btn")?.addEventListener("click", async () => {
    const newPw = document.getElementById("modal-reset-new-pw")?.value || "";
    if (!newPw) return;
    try {
      await postJson(`/admin/users/${encodeURIComponent(userId)}/reset-password`, {
        new_password: newPw,
      });
      setBanner("Password reset successfully.", "warning");
      closeUserModal();
    } catch (error) {
      setBanner(error.message, "error");
    }
  });
}

async function handleChangePassword() {
  try {
    await patchJson("/admin/auth/change-password", {
      current_password: document.getElementById("current-password")?.value || "",
      new_password: document.getElementById("new-password")?.value || "",
    });
    state.auth.authenticated = false;
    state.auth.user = null;
    state.auth.session = null;
    setBanner("Password changed. Please sign in again.", "warning");
    await loadAuthState();
  } catch (error) {
    setBanner(error.message, "error");
  }
}

function inferSoftnixHome() {
  const seededHome = state.overview?.instances
    ?.map((instance) => instance.instance_home)
    .find(Boolean);
  if (seededHome && seededHome.includes("/instances/")) {
    return seededHome.split("/instances/")[0];
  }
  return "/Users/rujirapongair/.softnix";
}

function normalizePath(value) {
  return (value || "").trim().replace(/\/+$/, "");
}

function slugifySegment(value) {
  return String(value || "")
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-");
}

function deriveInstanceValues(editor) {
  const env = (editor.env || "prod").trim() || "prod";
  const nameSlug = slugifySegment(editor.name);
  const ownerSlug = slugifySegment(editor.owner || editor.name);
  const derivedId = [nameSlug, slugifySegment(env)].filter(Boolean).join("-") || "";
  const instanceId =
    editor.mode === "edit"
      ? editor.targetId
      : editor.advanced
        ? (editor.instanceId || derivedId)
        : derivedId;
  const owner = editor.advanced ? (editor.owner || ownerSlug) : ownerSlug;
  const sourceConfig = editor.advanced ? editor.sourceConfig : "";
  return {
    env,
    owner,
    instanceId,
    sourceConfig,
  };
}

function defaultInstanceEditor() {
  const seed = state.overview?.instances?.[0];
  const runtime = seed?.runtime_config || {};
  const sandbox = runtime.sandbox || {};
  const profile = normalizeSandboxProfile(sandbox.profile || "balanced");
  return {
    mode: "create",
    advanced: false,
    targetId: "",
    instanceId: "",
    name: "",
    owner: "",
    env: "prod",
    repoRoot: seed?.working_dir || "/Volumes/Seagate/myapp/nanobot",
    nanobotBin: seed?.nanobot_bin || "/opt/anaconda3/bin/nanobot",
    gatewayPort: seed?.gateway_port ? String(seed.gateway_port) : "",
    sourceConfig: "",
    runtimeMode: SANDBOX_PROFILE_DEFAULTS[profile].runtimeMode,
    sandboxProfile: profile,
    runtimeOverrideOpen: false,
    sandboxImage: sandbox.image || "softnixclaw:latest",
    sandboxExecutionStrategy: sandbox.executionStrategy || SANDBOX_PROFILE_DEFAULTS[profile].sandboxExecutionStrategy,
    sandboxCpuLimit: sandbox.cpuLimit || SANDBOX_PROFILE_DEFAULTS[profile].sandboxCpuLimit,
    sandboxMemoryLimit: sandbox.memoryLimit || SANDBOX_PROFILE_DEFAULTS[profile].sandboxMemoryLimit,
    sandboxPidsLimit: sandbox.pidsLimit ? String(sandbox.pidsLimit) : SANDBOX_PROFILE_DEFAULTS[profile].sandboxPidsLimit,
    sandboxTmpfsSizeMb: sandbox.tmpfsSizeMb ? String(sandbox.tmpfsSizeMb) : SANDBOX_PROFILE_DEFAULTS[profile].sandboxTmpfsSizeMb,
    sandboxNetworkPolicy: sandbox.networkPolicy || SANDBOX_PROFILE_DEFAULTS[profile].sandboxNetworkPolicy,
    sandboxTimeoutSeconds: sandbox.timeoutSeconds ? String(sandbox.timeoutSeconds) : SANDBOX_PROFILE_DEFAULTS[profile].sandboxTimeoutSeconds,
  };
}

function buildInstanceEditorFromInstance(instance) {
  return {
    mode: "edit",
    advanced: true,
    targetId: instance.id,
    instanceId: instance.id,
    name: instance.name || "",
    owner: instance.owner || "",
    env: instance.env || "prod",
    repoRoot: instance.working_dir || "/Volumes/Seagate/myapp/nanobot",
    nanobotBin: instance.nanobot_bin || "/opt/anaconda3/bin/nanobot",
    gatewayPort: instance.gateway_port ? String(instance.gateway_port) : "",
    sourceConfig: instance.config_path || "",
    runtimeMode: instance.runtime_config?.mode || "host",
    sandboxProfile: normalizeSandboxProfile(instance.runtime_config?.sandbox?.profile || "balanced"),
    runtimeOverrideOpen: false,
    sandboxImage: instance.runtime_config?.sandbox?.image || "softnixclaw:latest",
    sandboxExecutionStrategy: instance.runtime_config?.sandbox?.executionStrategy || "persistent",
    sandboxCpuLimit: instance.runtime_config?.sandbox?.cpuLimit || "",
    sandboxMemoryLimit: instance.runtime_config?.sandbox?.memoryLimit || "",
    sandboxPidsLimit: instance.runtime_config?.sandbox?.pidsLimit ? String(instance.runtime_config.sandbox.pidsLimit) : "256",
    sandboxTmpfsSizeMb: instance.runtime_config?.sandbox?.tmpfsSizeMb ? String(instance.runtime_config.sandbox.tmpfsSizeMb) : "128",
    sandboxNetworkPolicy: instance.runtime_config?.sandbox?.networkPolicy || "default",
    sandboxTimeoutSeconds: instance.runtime_config?.sandbox?.timeoutSeconds ? String(instance.runtime_config.sandbox.timeoutSeconds) : "30",
  };
}

function openCreateInstanceEditor() {
  state.instanceEditor = defaultInstanceEditor();
  state.instanceEditor.mode = "create";
  state.instanceCreateOpen = true;
  state.selectedInstanceId = "";
  state.deleteCandidateId = "";
  state.instanceWorkspaceTab = "manage";
}

function selectedInstance() {
  return state.overview?.instances?.find((instance) => instance.id === state.selectedInstanceId) || null;
}

function renderSummary() {
  const summary = state.overview.summary;
  const runtimeMetrics = state.overviewRuntimeAuditByInstance || {};
  const operationCount = Object.values(runtimeMetrics)
    .reduce((total, entry) => total + Number(entry?.summary?.event_count || 0), 0);
  const okCount = Object.values(runtimeMetrics)
    .reduce((total, entry) => total + Number(entry?.events?.filter((event) => event.status === "ok").length || 0), 0);
  const errorCount = Object.values(runtimeMetrics)
    .reduce((total, entry) => total + Number(entry?.events?.filter((event) => event.status === "error").length || 0), 0);
  const metrics = [
    {
      label: "Number of Instances",
      value: formatNumber(summary.instance_count),
      note: "Managed instances",
    },
    {
      label: "Sessions",
      value: formatNumber(summary.session_count),
      note: "Sessions across all instances",
    },
    {
      label: "Operations",
      value: formatNumber(operationCount),
      note: "Runtime-audit events sampled",
    },
    {
      label: "Status OK / Error",
      value: `${formatNumber(okCount)} / ${formatNumber(errorCount)}`,
      note: "Latest runtime-audit sample",
    },
  ];

  document.getElementById("summary-grid").innerHTML = metrics
    .map(
      (item) => `
        <article class="metric-card">
          <p class="metric-label">${escapeHtml(item.label)}</p>
          <h3 class="metric-value">${escapeHtml(item.value)}</h3>
          <p class="metric-note">${escapeHtml(item.note)}</p>
        </article>
      `,
    )
    .join("");
}

function renderOverviewDashboard() {
  const instanceTarget = document.getElementById("overview-instance-metrics");
  const topUsageTarget = document.getElementById("overview-top-usage");
  const opsTarget = document.getElementById("overview-ops-table");
  const opsFilterEl = document.getElementById("overview-ops-instance-filter");
  if (!instanceTarget || !topUsageTarget || !opsTarget) {
    return;
  }

  const runtimeByInstance = state.overviewRuntimeAuditByInstance || {};
  const instances = state.overview.instances || [];

  if (opsFilterEl) {
    const currentValue = state.overviewOpsInstanceId;
    opsFilterEl.innerHTML = `
      <option value="all" ${currentValue === "all" ? "selected" : ""}>All Instances</option>
      ${instances.map(inst => `<option value="${escapeHtml(inst.id)}" ${currentValue === inst.id ? "selected" : ""}>${escapeHtml(inst.name)}</option>`).join("")}
    `;
    if (!opsFilterEl.onchange) {
      opsFilterEl.onchange = (e) => {
        state.overviewOpsInstanceId = e.target.value;
        renderOverviewDashboard();
      };
    }
  }

  const allEvents = Object.values(runtimeByInstance).flatMap((entry) => entry?.events || []);
  const filteredEvents = state.overviewOpsInstanceId === "all" 
    ? allEvents 
    : (runtimeByInstance[state.overviewOpsInstanceId]?.events || []);

  const topModels = new Map();
  const topChannels = new Map();
  instances.forEach((instance) => {
    const model = String(instance.model || "unknown");
    topModels.set(model, (topModels.get(model) || 0) + 1);
  });
  (state.activity?.events || []).forEach((event) => {
    const channel = String(event.channel || "");
    if (!channel || channel === "system") return;
    topChannels.set(channel, (topChannels.get(channel) || 0) + 1);
  });
  const topBarRows = (counter, emptyLabel) => {
    const rows = [...counter.entries()].sort((left, right) => right[1] - left[1]).slice(0, 5);
    if (!rows.length) return `<p class="meta">${escapeHtml(emptyLabel)}</p>`;
    const max = Math.max(1, ...rows.map((item) => item[1]));
    return `
      <div class="overview-hbars">
        ${rows
          .map(([name, count]) => `
            <div class="overview-hbar-row">
              <div class="overview-hbar-meta">
                <span class="overview-hbar-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
                <span class="overview-hbar-count">${escapeHtml(formatNumber(count))}</span>
              </div>
              <div class="overview-hbar-track">
                <div class="overview-hbar-fill" style="width:${Math.max(8, Math.round((count / max) * 100))}%"></div>
              </div>
            </div>
          `)
          .join("")}
      </div>
    `;
  };
  const instanceHealthRows = instances.map((instance) => {
    const events = runtimeByInstance[instance.id]?.events || [];
    const ok = events.filter((event) => event.status === "ok").length;
    const error = events.filter((event) => event.status === "error").length;
    const total = ok + error;
    const okPct = total > 0 ? (ok / total) * 100 : 0;
    const errPct = total > 0 ? (error / total) * 100 : 0;
    const rate = total > 0 ? `${okPct.toFixed(1)}%` : "n/a";
    return { instance, ok, error, total, okPct, errPct, rate };
  });

  instanceTarget.innerHTML = `
    <div class="overview-instance-panels">
      <div class="item-card">
        <h4>Sessions by Instance</h4>
        <div class="overview-hbars">
          ${instances.map((instance) => `
            <div class="overview-hbar-row">
              <div class="overview-hbar-meta">
                <span class="overview-hbar-name" title="${escapeHtml(instance.id)}">${escapeHtml(instance.name)}</span>
                <span class="overview-hbar-count">${escapeHtml(String(instance.sessions?.count || 0))}</span>
              </div>
              <div class="overview-hbar-track">
                <div
                  class="overview-hbar-fill"
                  style="width:${Math.max(
                    8,
                    Math.round(
                      ((instance.sessions?.count || 0) / Math.max(1, ...instances.map((item) => item.sessions?.count || 0))) * 100,
                    ),
                  )}%"
                ></div>
              </div>
            </div>
          `).join("") || `<p class="meta">No session data yet</p>`}
        </div>
      </div>
      <div class="item-card">
        <h4>Success Rate by Instance</h4>
        <div class="overview-stacked-list">
          ${instanceHealthRows.map((row) => `
            <div class="overview-stack-row">
              <div class="overview-stack-meta">
                <span class="overview-hbar-name">${escapeHtml(row.instance.name)}</span>
                <span class="overview-hbar-count">${escapeHtml(row.rate)} (${escapeHtml(String(row.ok))}/${escapeHtml(String(row.total || 0))})</span>
              </div>
              <div class="overview-stack-track">
                <div class="overview-stack-ok" style="width:${row.okPct.toFixed(2)}%"></div>
                <div class="overview-stack-error" style="width:${row.errPct.toFixed(2)}%"></div>
              </div>
            </div>
          `).join("") || `<p class="meta">No runtime events yet</p>`}
        </div>
      </div>
    </div>
  `;

  topUsageTarget.innerHTML = `
    <div class="stack">
      <div class="item-card">
        <h4>Top Models</h4>
        ${topBarRows(topModels, "No model data yet")}
      </div>
      <div class="item-card">
        <h4>Top Channels</h4>
        ${topBarRows(topChannels, "No channel events yet")}
      </div>
    </div>
  `;

  const operationBuckets = new Map();
  filteredEvents.forEach((event) => {
    const instanceId = String(event.instance_id || "unknown");
    const operation = String(event.operation || "unknown");
    const key = `${instanceId}::${operation}`;
    const bucket = operationBuckets.get(key) || { instanceId, operation, ok: 0, error: 0, running: 0 };
    const status = String(event.status || "ok");
    if (status === "error") bucket.error += 1;
    else if (status === "running") bucket.running += 1;
    else bucket.ok += 1;
    operationBuckets.set(key, bucket);
  });
  const operationRows = [...operationBuckets.values()]
    .sort((left, right) => (right.ok + right.error + right.running) - (left.ok + left.error + left.running))
    .slice(0, 40);
  opsTarget.innerHTML = `
    <div class="table-wrap">
      <table class="instances-table">
        <thead><tr><th>Instance</th><th>Operation</th><th>OK</th><th>Error</th><th>Running</th><th>Total</th></tr></thead>
        <tbody>
          ${operationRows.map((row) => `
            <tr>
              <td>${escapeHtml(row.instanceId)}</td>
              <td>${escapeHtml(row.operation)}</td>
              <td>${escapeHtml(String(row.ok))}</td>
              <td>${escapeHtml(String(row.error))}</td>
              <td>${escapeHtml(String(row.running))}</td>
              <td>${escapeHtml(String(row.ok + row.error + row.running))}</td>
            </tr>
          `).join("") || `<tr><td colspan="6" class="table-empty">No operation data yet</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}

async function renderActivityHeatmap() {
  const target = document.getElementById("overview-activity-heatmap");
  if (!target) return;

  try {
    const response = await fetch("/admin/analytics/activity-heatmap?days=30");
    const data = await response.json();

    const heatmap = data.heatmap || {};
    const allCellValues = Object.values(heatmap).flat();
    const maxHeatmapValue = Math.max(1, ...allCellValues);
    const totalEvents = data.total_events || 0;

    // --- Bar Chart (by day of week, aggregated from heatmap) ---
    const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const dayTotals = days.map(day =>
      (heatmap[day] || []).reduce((sum, v) => sum + v, 0)
    );
    const maxDayTotal = Math.max(1, ...dayTotals);

    const barChartHtml = `
      <div class="act-barchart">
        <div class="act-barchart-bars">
          ${days.map((day, i) => {
            const count = dayTotals[i];
            const pct = Math.round((count / maxDayTotal) * 100);
            return `
              <div class="act-barchart-col">
                <div class="act-barchart-count">${count > 0 ? count : ""}</div>
                <div class="act-barchart-track">
                  <div class="act-barchart-bar" style="height:${pct}%" title="${day}: ${count} event${count !== 1 ? "s" : ""}"></div>
                </div>
                <div class="act-barchart-label">${day}</div>
              </div>
            `;
          }).join("")}
        </div>
      </div>
    `;

    // --- Heatmap ---
    const hourLabels = Array.from({length: 24}, (_, i) =>
      i % 3 === 0 ? String(i).padStart(2, "0") : ""
    );

    const heatmapHtml = `
      <div class="act-heatmap">
        <div class="act-heatmap-inner">
          <div class="act-heatmap-header">
            <div class="act-heatmap-daylabel"></div>
            ${hourLabels.map(h => `<div class="act-heatmap-hourlabel">${h}</div>`).join("")}
          </div>
          ${days.map(day => `
            <div class="act-heatmap-row">
              <div class="act-heatmap-daylabel">${day}</div>
              ${(heatmap[day] || Array(24).fill(0)).map((count, hour) => {
                const intensity = count > 0 ? Math.min(4, Math.ceil((count / maxHeatmapValue) * 4)) : 0;
                return `<div class="act-cell act-cell-${intensity}" title="${day} ${String(hour).padStart(2,"0")}:00 — ${count} event${count !== 1 ? "s" : ""}"></div>`;
              }).join("")}
            </div>
          `).join("")}
        </div>
      </div>
    `;

    target.innerHTML = `
      <div class="item-card act-card">
        <div class="act-header">
          <div>
            <div class="act-title">Activity by Day and Hour</div>
            <div class="act-subtitle">Last ${data.days} days &middot; <strong>${formatNumber(totalEvents)}</strong> total events</div>
          </div>
        </div>
        <div class="act-divider"></div>
        ${barChartHtml}
        <div class="act-divider"></div>
        ${heatmapHtml}
      </div>
    `;
  } catch (error) {
    target.innerHTML = `<div class="item-card"><p class="meta">Failed to load activity data.</p></div>`;
  }
}

function renderInstances() {
  const target = document.getElementById("instances-list-panel");
  if (!target) return;
  const rows = state.overview.instances
    .map((instance) => {
      const runtimeStatus = instance.runtime.status || "unknown";
      const runtimeLabel = runtimeStatus.charAt(0).toUpperCase() + runtimeStatus.slice(1);
      const runtimeSeverity =
        runtimeStatus === "running"
          ? "ok"
          : runtimeStatus === "stopped"
            ? "warning"
            : runtimeStatus === "unknown"
              ? "info"
              : "neutral";
      const sessionLabel = instance.sessions.latest_updated_at || "No session yet";
      const providerLabel = instance.selected_provider || "Unresolved";
      const selectedClass = state.selectedInstanceId === instance.id ? " is-selected" : "";
      return `
        <tr class="instance-row${selectedClass}">
          <td>
            <div class="table-primary">${escapeHtml(instance.name)}</div>
            <div class="table-secondary">${escapeHtml(instance.id)}</div>
          </td>
          <td><span class="badge ${badgeClass(runtimeSeverity)}">${escapeHtml(runtimeLabel)}</span></td>
          <td>${escapeHtml(providerLabel)}</td>
          <td>${escapeHtml(instance.sessions.count)}</td>
          <td>${escapeHtml(instance.cron.jobs)}</td>
          <td class="table-ellipsis" title="${escapeHtml(sessionLabel)}">${escapeHtml(sessionLabel)}</td>
          <td>
            <div class="table-actions">
              ${["start", "stop", "restart"].map((action) => {
                const supported = (instance.runtime.actions || []).includes(action);
                const key = `instance:${instance.id}:${action}`;
                const instanceBusy = state.busyKey && state.busyKey.startsWith(`instance:${instance.id}:`);
                const isThisActionBusy = state.busyKey === key;
                const disabledByState =
                  (action === "start" && runtimeStatus === "running") ||
                  (action === "stop" && runtimeStatus === "stopped");
                const disabled = !supported || disabledByState || instanceBusy ? "disabled" : "";
                const cssClass = action === "restart" ? "primary-button is-small" : "secondary-button is-small";
                const loadingLabels = { start: "Starting…", restart: "Restarting…", stop: "Stopping…" };
                const spinner = isThisActionBusy ? `<span class="btn-spinner"></span>` : "";
                const label = isThisActionBusy ? loadingLabels[action] : (action.charAt(0).toUpperCase() + action.slice(1));
                return `<button class="${cssClass}" data-instance-action="${escapeHtml(key)}" ${disabled}>${spinner}${label}</button>`;
              }).join("")}
              <button class="secondary-button is-small" data-instance-edit="${escapeHtml(instance.id)}">Manage</button>
              <button class="secondary-button is-small" data-instance-delete="${escapeHtml(instance.id)}">Delete</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");

  target.innerHTML = `
    <div class="instance-toolbar">
      <div class="table-primary">${escapeHtml(state.overview.instances.length)} instances</div>
      <div class="inline-actions">
        <button id="instance-create-shortcut" class="primary-button is-small">Add Instance</button>
      </div>
    </div>
    <div class="table-wrap">
      <table class="instances-table">
        <thead>
          <tr>
            <th>Instance</th>
            <th>Status</th>
            <th>Provider</th>
            <th>Sessions</th>
            <th>Cron</th>
            <th>Latest Activity</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>${rows || `<tr><td colspan="7" class="table-empty">No instances registered yet.</td></tr>`}</tbody>
      </table>
    </div>
  `;

  target.querySelectorAll("[data-instance-action]").forEach((button) => {
    button.addEventListener("click", () => handleInstanceAction(button.dataset.instanceAction));
  });
  target.querySelectorAll("[data-instance-edit]").forEach((button) => {
    button.addEventListener("click", () => selectInstanceForEdit(button.dataset.instanceEdit));
  });
  target.querySelectorAll("[data-instance-delete]").forEach((button) => {
    button.addEventListener("click", () => handleInstanceDelete(button.dataset.instanceDelete));
  });
  document.getElementById("instance-create-shortcut")?.addEventListener("click", () => {
    openCreateInstanceEditor();
    renderInstances();
    syncLocationState();
  });
  renderInstanceWorkspace();
}

function renderInstanceWorkspace() {
  const selected = selectedInstance();
  const title = document.getElementById("instance-workspace-title");
  const editor = state.instanceEditor || defaultInstanceEditor();
  if (!selected && editor.mode === "create" && state.instanceCreateOpen) {
    state.instanceWorkspaceTab = "manage";
  }
  if (title) {
    title.textContent = selected
      ? `${selected.name} (${selected.id})`
      : editor.mode === "create" && state.instanceCreateOpen
        ? "Create New Instance"
        : "No instance selected";
  }
  renderInstanceWorkspaceTabs();
  syncInstanceWorkspacePanels();
  renderInstanceWorkspaceContent();
  syncLocationState();
}

function renderInstanceWorkspaceTabs() {
  const target = document.getElementById("instance-workspace-tabs");
  if (!target) return;
  const selected = selectedInstance();
  const editor = state.instanceEditor || defaultInstanceEditor();
  if (!selected && editor.mode === "create") {
    target.innerHTML = "";
    return;
  }
  const tabLabel = (id, label) => `<button class="console-tab ${state.instanceWorkspaceTab === id ? "is-active" : ""}" data-workspace-tab="${id}">${label}</button>`;
  const tabs = [tabLabel("manage", "Manage")];
  if (selected || editor.mode !== "create") {
    tabs.push(tabLabel("channels", "Channels"));
    tabs.push(tabLabel("providers", "Providers & MCP"));
    tabs.push(tabLabel("memory", "Memory"));
    tabs.push(tabLabel("skills", "Skills"));
    tabs.push(tabLabel("schedules", "Schedules"));
    tabs.push(tabLabel("security", "Security"));
    tabs.push(tabLabel("runtime-audit", "Runtime Audit"));
    tabs.push(tabLabel("execution-visualize", "Execution Visualize"));
  }
  target.innerHTML = tabs.join("");
  target.querySelectorAll("[data-workspace-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.instanceWorkspaceTab = safeWorkspaceTab(button.dataset.workspaceTab);
      syncInstanceWorkspacePanels();
      renderInstanceWorkspaceContent();
      renderInstanceWorkspaceTabs();
      syncLocationState();
    });
  });
}

function syncInstanceWorkspacePanels() {
  const panels = {
    manage: document.getElementById("instance-workspace-manage"),
    channels: document.getElementById("instance-workspace-channels"),
    providers: document.getElementById("instance-workspace-providers"),
    memory: document.getElementById("instance-workspace-memory"),
    skills: document.getElementById("instance-workspace-skills"),
    schedules: document.getElementById("instance-workspace-schedules"),
    security: document.getElementById("instance-workspace-security"),
    "runtime-audit": document.getElementById("instance-workspace-runtime-audit"),
    "execution-visualize": document.getElementById("instance-workspace-execution-visualize"),
  };
  Object.entries(panels).forEach(([name, element]) => {
    if (!element) return;
    element.classList.toggle("is-hidden", state.instanceWorkspaceTab !== name);
  });
}

function renderInstanceWorkspaceContent() {
  const selected = selectedInstance();
  if (state.instanceWorkspaceTab === "manage") {
    renderInstanceForm();
    return;
  }
  if (!selected) {
    ["instance-workspace-channels", "instance-workspace-providers", "instance-workspace-memory", "instance-workspace-skills", "instance-workspace-schedules", "instance-workspace-security", "instance-workspace-runtime-audit", "instance-workspace-execution-visualize"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) {
        el.innerHTML = `<div class="item-card"><h4>Select an instance</h4><p class="meta">Choose an instance from the table to manage ${id.split("-").pop()}.</p></div>`;
      }
    });
    return;
  }
  renderSelectedInstanceChannels(selected);
  renderSelectedInstanceProviders(selected);
  renderSelectedInstanceMemory(selected);
  renderSelectedInstanceSkills(selected);
  renderSelectedInstanceSchedules(selected);
  renderSelectedInstanceSecurity(selected);
  renderSelectedInstanceRuntimeAudit(selected);
  renderSelectedInstanceExecutionVisualize(selected);
}

function renderSelectedInstanceChannels(instance) {
  const target = document.getElementById("instance-workspace-channels");
  if (!target) return;
  const channels = instance.channels || [];
  if (!channels.length) {
    target.innerHTML = `<div class="item-card"><h4>No channels</h4><p class="meta">No channel configuration found for this instance.</p></div>`;
    return;
  }
  const preferred = state.channelFocusByInstance[instance.id]
    || (channels.find((item) => item.name === "telegram")?.name)
    || channels[0].name;
  const selectedChannel = channels.find((item) => item.name === preferred) || channels[0];
  state.channelFocusByInstance[instance.id] = selectedChannel.name;
  const key = `${instance.id}:${selectedChannel.name}`;
  const disabled = state.busyKey === key ? "disabled" : "";
  const pendingRequests = (state.accessRequests?.requests || []).filter(
    (item) => item.instance_id === instance.id && item.channel === selectedChannel.name,
  );
  const originalChannel = state.channels.find(
    (item) => item.instance_id === instance.id && item.name === selectedChannel.name,
  );
  const currentAllow = originalChannel && Array.isArray(originalChannel.allow_from)
    ? originalChannel.allow_from.join("\n")
    : "";
  const channelButtons = channels
    .map((channel) => `
      <button
        class="console-tab ${channel.name === selectedChannel.name ? "is-active" : ""}"
        data-channel-focus="${escapeHtml(instance.id)}:${escapeHtml(channel.name)}"
      >
        ${escapeHtml(channel.name)}
      </button>
    `)
    .join("");
  const telegramSettings = selectedChannel.name === "telegram"
    ? `
      <div class="field">
        <label for="channel-token-${escapeHtml(key)}">Telegram Token</label>
        <input
          id="channel-token-${escapeHtml(key)}"
          data-channel-setting="${escapeHtml(key)}"
          data-setting-key="token"
          value="${escapeHtml(selectedChannel.settings?.token || "")}"
          placeholder="123456:ABC..."
          ${disabled}
        >
      </div>
      <div class="field">
        <label for="channel-proxy-${escapeHtml(key)}">Telegram Proxy (optional)</label>
        <input
          id="channel-proxy-${escapeHtml(key)}"
          data-channel-setting="${escapeHtml(key)}"
          data-setting-key="proxy"
          data-nullable="true"
          value="${escapeHtml(selectedChannel.settings?.proxy || "")}"
          placeholder="http://127.0.0.1:7890"
          ${disabled}
        >
      </div>
      <div class="field">
        <label>
          <input
            type="checkbox"
            data-channel-setting="${escapeHtml(key)}"
            data-setting-key="reply_to_message"
            ${selectedChannel.settings?.reply_to_message ? "checked" : ""}
            ${disabled}
          >
          Reply as threaded reply
        </label>
      </div>
    `
    : `<p class="meta">Quick editor currently supports token settings for Telegram. Use config.json editor for advanced channel-specific fields.</p>`;
  const pendingHtml = pendingRequests.length
    ? `
      <div class="item-card channel-pending-card">
        <div class="row-between">
          <h4>Pending Access Requests</h4>
          <span class="badge is-orange">${escapeHtml(pendingRequests.length)} pending</span>
        </div>
        <div class="stack">
          ${pendingRequests
            .map((request) => {
              const approveBusyKey = `approve:${instance.id}:${selectedChannel.name}:${request.sender_id}`;
              const rejectBusyKey = `reject:${instance.id}:${selectedChannel.name}:${request.sender_id}`;
              const approveDisabled = state.busyKey === approveBusyKey ? "disabled" : "";
              const rejectDisabled = state.busyKey === rejectBusyKey ? "disabled" : "";
              return `
                <div class="channel-pending-row">
                  <div>
                    <p><strong>Sender:</strong> ${escapeHtml(request.sender_id)}</p>
                    <p class="meta">Chat: ${escapeHtml(request.chat_id || "-")} · Seen: ${escapeHtml(request.count || 1)} · Last: ${escapeHtml(request.last_seen || "")}</p>
                    <p class="meta">${escapeHtml(request.last_content || "")}</p>
                  </div>
                  <div class="inline-actions">
                    <button
                      class="primary-button is-small"
                      data-access-approve="1"
                      data-instance-id="${escapeHtml(instance.id)}"
                      data-channel-name="${escapeHtml(selectedChannel.name)}"
                      data-sender-id="${escapeHtml(request.sender_id)}"
                      ${approveDisabled}
                    >
                      Accept
                    </button>
                    <button
                      class="secondary-button is-small"
                      data-access-reject="1"
                      data-instance-id="${escapeHtml(instance.id)}"
                      data-channel-name="${escapeHtml(selectedChannel.name)}"
                      data-sender-id="${escapeHtml(request.sender_id)}"
                      ${rejectDisabled}
                    >
                      Reject
                    </button>
                  </div>
                </div>
              `;
            })
            .join("")}
        </div>
      </div>
    `
    : `
      <div class="item-card channel-pending-card">
        <h4>Pending Access Requests</h4>
        <p class="meta">No pending sender requests for this channel.</p>
      </div>
    `;
  target.innerHTML = `
    <div class="channel-workspace">
      <div class="channel-nav">
        <p class="eyebrow">Channels</p>
        <div class="channel-tab-list">${channelButtons}</div>
      </div>
      <div class="item-card">
        <div class="row-between">
          <h4>${escapeHtml(selectedChannel.name)}</h4>
          <span class="badge ${badgeClass(selectedChannel.enabled ? "ok" : "neutral")}">${selectedChannel.enabled ? "Enabled" : "Disabled"}</span>
        </div>
        <div class="field">
          <label>
            <input type="checkbox" data-channel-enabled="${escapeHtml(key)}" ${selectedChannel.enabled ? "checked" : ""} ${disabled}>
            Enabled
          </label>
        </div>
        ${telegramSettings}
        <div class="field">
          <label for="allow-${escapeHtml(key)}">Allowlist</label>
          <textarea id="allow-${escapeHtml(key)}" data-channel-allow="${escapeHtml(key)}" ${disabled} placeholder="One user identifier per line">${escapeHtml(currentAllow)}</textarea>
        </div>
        <div class="inline-actions">
          <button class="primary-button is-small" data-channel-save="${escapeHtml(key)}" ${disabled}>Save</button>
        </div>
        ${pendingHtml}
      </div>
    </div>
  `;
  target.querySelectorAll("[data-channel-focus]").forEach((button) => {
    button.addEventListener("click", () => {
      const [instanceId, channelName] = button.dataset.channelFocus.split(":");
      state.channelFocusByInstance[instanceId] = channelName;
      renderSelectedInstanceChannels(instance);
    });
  });
  target.querySelectorAll("[data-channel-save]").forEach((button) => {
    button.addEventListener("click", () => handleChannelSave(button.dataset.channelSave));
  });
  target.querySelectorAll("[data-access-approve]").forEach((button) => {
    button.addEventListener("click", () => {
      handleAccessRequestApprove({
        instanceId: button.dataset.instanceId,
        channelName: button.dataset.channelName,
        senderId: button.dataset.senderId,
      });
    });
  });
  target.querySelectorAll("[data-access-reject]").forEach((button) => {
    button.addEventListener("click", () => {
      handleAccessRequestReject({
        instanceId: button.dataset.instanceId,
        channelName: button.dataset.channelName,
        senderId: button.dataset.senderId,
      });
    });
  });
}

function renderSelectedInstanceProviders(instance) {
  const target = document.getElementById("instance-workspace-providers");
  if (!target) return;
  const providers = instance.providers || [];
  const servers = instance.mcp?.servers || [];
  const preferredMode = state.providerModeByInstance[instance.id] || "providers";
  const mode = preferredMode === "mcp" || !providers.length ? "mcp" : "providers";
  state.providerModeByInstance[instance.id] = mode;
  const defaultKey = `provider-default:${instance.id}`;
  const defaultBusy = state.busyKey === defaultKey ? "disabled" : "";
  const providerOptions = ['<option value="auto">auto</option>']
    .concat(
      providers.map(
        (provider) => `<option value="${escapeHtml(provider.name)}" ${provider.name === instance.selected_provider ? "selected" : ""}>${escapeHtml(provider.label || provider.name)}</option>`,
      ),
    )
    .join("");
  const selectedProviderName = state.providerFocusByInstance[instance.id]
    || instance.selected_provider
    || providers[0]?.name;
  const selectedProvider = providers.find((item) => item.name === selectedProviderName) || providers[0] || null;
  const selectedServerName = state.mcpFocusByInstance[instance.id] || servers[0]?.name;
  const selectedServer = servers.find((item) => item.name === selectedServerName) || servers[0] || null;
  if (selectedProvider) {
    state.providerFocusByInstance[instance.id] = selectedProvider.name;
  }
  if (selectedServer) {
    state.mcpFocusByInstance[instance.id] = selectedServer.name;
  }
  const mcpCreateOpen = !!state.mcpCreateOpenByInstance[instance.id];

  const leftList = mode === "providers"
    ? providers.map((provider) => `
      <button class="console-tab provider-subtab ${provider.name === selectedProvider?.name ? "is-active" : ""}" data-provider-focus="${escapeHtml(instance.id)}:${escapeHtml(provider.name)}">
        ${escapeHtml(provider.label)}
      </button>
    `).join("")
    : servers.map((server) => `
      <button class="console-tab provider-subtab ${server.name === selectedServer?.name ? "is-active" : ""}" data-mcp-focus="${escapeHtml(instance.id)}:${escapeHtml(server.name)}">
        ${escapeHtml(server.name)}
      </button>
    `).join("");

  const providerEditor = selectedProvider
    ? (() => {
      const key = `provider:${instance.id}:${selectedProvider.name}`;
      const disabled = state.busyKey === key ? "disabled" : "";
      return `
        <div class="item-card">
          <div class="row-between">
            <h4>${escapeHtml(selectedProvider.label)}</h4>
            <span class="badge ${badgeClass(selectedProvider.configured ? "ok" : "neutral")}">${selectedProvider.configured ? "Configured" : "Not set"}</span>
          </div>
          <div class="field">
            <label>API Base</label>
            <input data-provider-base="${escapeHtml(key)}" value="${escapeHtml(selectedProvider.api_base || "")}" ${disabled}>
          </div>
          <div class="field">
            <label>API Key</label>
            <input data-provider-key="${escapeHtml(key)}" value="" placeholder="${escapeHtml(selectedProvider.api_key_masked || "")}" ${disabled}>
          </div>
          <div class="field">
            <label>Extra Headers (JSON)</label>
            <textarea data-provider-headers="${escapeHtml(key)}" ${disabled}>${escapeHtml(JSON.stringify(selectedProvider.extra_headers || {}, null, 2))}</textarea>
          </div>
          <div class="inline-actions">
            <button class="primary-button is-small" data-provider-save="${escapeHtml(key)}" ${disabled}>Save</button>
            <button class="secondary-button is-small" data-provider-validate="${escapeHtml(key)}" ${disabled}>Validate</button>
          </div>
        </div>
      `;
    })()
    : `<div class="item-card"><p class="meta">No providers available.</p></div>`;

  const mcpEditor = selectedServer
    ? (() => {
      const key = `mcp:${instance.id}:${selectedServer.name}`;
      const disabled = state.busyKey === key ? "disabled" : "";
      return `
        <div class="item-card">
          <div class="row-between">
            <h4>${escapeHtml(selectedServer.name)}</h4>
            <span class="badge ${badgeClass("ok")}">${escapeHtml(selectedServer.type || "auto")}</span>
          </div>
          <div class="field"><label>Command</label><input data-mcp-command="${escapeHtml(key)}" value="${escapeHtml(selectedServer.command || "")}" ${disabled}></div>
          <div class="field"><label>URL</label><input data-mcp-url="${escapeHtml(key)}" value="${escapeHtml(selectedServer.url || "")}" ${disabled}></div>
          <div class="field"><label>Timeout</label><input data-mcp-timeout="${escapeHtml(key)}" type="number" value="${escapeHtml(selectedServer.tool_timeout || 30)}" ${disabled}></div>
          <div class="field"><label>Args (JSON array)</label><textarea data-mcp-args="${escapeHtml(key)}" ${disabled}>${escapeHtml(JSON.stringify(selectedServer.args || [], null, 2))}</textarea></div>
          <div class="field"><label>Headers (JSON)</label><textarea data-mcp-headers="${escapeHtml(key)}" ${disabled}>${escapeHtml(JSON.stringify(selectedServer.headers || {}, null, 2))}</textarea></div>
          <div class="inline-actions">
            <button class="primary-button is-small" data-mcp-save="${escapeHtml(key)}" ${disabled}>Save</button>
            <button class="secondary-button is-small" data-mcp-validate="${escapeHtml(key)}" ${disabled}>Validate</button>
            <button class="secondary-button is-small" data-mcp-delete="${escapeHtml(key)}" ${disabled}>Delete</button>
          </div>
        </div>
      `;
    })()
    : `<div class="item-card"><p class="meta">No MCP servers for this instance yet.</p></div>`;

  const createKey = `mcp-create:${instance.id}`;
  const createDisabled = state.busyKey === createKey ? "disabled" : "";
  const mcpEmpty = servers.length === 0;
  const showMcpCreate = mode === "mcp" && (mcpCreateOpen || mcpEmpty);
  const showMcpEditor = mode === "mcp" && !showMcpCreate;
  target.innerHTML = `
    <div class="stack">
      <div class="item-card">
        <h4>Default Routing</h4>
        <div class="field"><label>Model</label><input data-default-model="${escapeHtml(instance.id)}" value="${escapeHtml(instance.model || "")}" ${defaultBusy}></div>
        <div class="field"><label>Provider</label><select data-default-provider="${escapeHtml(instance.id)}" ${defaultBusy}>${providerOptions}</select></div>
        <div class="inline-actions"><button class="primary-button is-small" data-provider-default-save="${escapeHtml(instance.id)}" ${defaultBusy}>Save Defaults</button></div>
      </div>
      <div class="provider-workspace">
        <div class="provider-nav">
          <div class="provider-mode-switch" role="tablist" aria-label="Provider sections">
            <button class="console-tab provider-main-tab ${mode === "providers" ? "is-active" : ""}" data-provider-mode="${escapeHtml(instance.id)}:providers">Providers</button>
            <button class="console-tab provider-main-tab ${mode === "mcp" ? "is-active" : ""}" data-provider-mode="${escapeHtml(instance.id)}:mcp">MCP Servers</button>
          </div>
          <p class="eyebrow provider-subtitle">${mode === "providers" ? "Provider List" : "MCP Server List"}</p>
          <div class="provider-tab-list">${leftList || `<p class="meta">${mode === "providers" ? "No providers" : "No MCP servers"}</p>`}</div>
          ${mode === "mcp" ? `
            <div class="inline-actions provider-nav-actions">
              <button class="secondary-button is-small" data-mcp-create-toggle="${escapeHtml(instance.id)}">
                ${showMcpCreate ? "Back to Editor" : "Add MCP Server"}
              </button>
            </div>
          ` : ""}
        </div>
        <div class="stack">
          ${mode === "providers" ? providerEditor : ""}
          ${showMcpEditor ? mcpEditor : ""}
          ${showMcpCreate ? `
          <div class="item-card">
            <h4>Add MCP Server</h4>
            <div class="field"><label>Server Name</label><input data-mcp-create-name="${escapeHtml(instance.id)}" ${createDisabled}></div>
            <div class="field">
              <label>Type</label>
              <select data-mcp-create-type="${escapeHtml(instance.id)}" ${createDisabled}>
                <option value="streamableHttp">streamableHttp</option>
                <option value="sse">sse</option>
                <option value="stdio">stdio</option>
              </select>
            </div>
            <div class="field"><label>Command</label><input data-mcp-create-command="${escapeHtml(instance.id)}" ${createDisabled}></div>
            <div class="field"><label>URL</label><input data-mcp-create-url="${escapeHtml(instance.id)}" ${createDisabled}></div>
            <div class="field"><label>Tool Timeout</label><input data-mcp-create-timeout="${escapeHtml(instance.id)}" type="number" value="30" ${createDisabled}></div>
            <div class="field"><label>Args (JSON array)</label><textarea data-mcp-create-args="${escapeHtml(instance.id)}" ${createDisabled}>[]</textarea></div>
            <div class="field"><label>Headers (JSON object)</label><textarea data-mcp-create-headers="${escapeHtml(instance.id)}" ${createDisabled}>{}</textarea></div>
            <div class="inline-actions">
              <button class="primary-button is-small" data-mcp-create-save="${escapeHtml(instance.id)}" ${createDisabled}>Add Server</button>
              ${servers.length > 0 ? `<button class="secondary-button is-small" data-mcp-create-cancel="${escapeHtml(instance.id)}" ${createDisabled}>Cancel</button>` : ""}
            </div>
          </div>` : ""}
        </div>
      </div>
    </div>
  `;
  target.querySelectorAll("[data-provider-mode]").forEach((button) => button.addEventListener("click", () => {
    const [instanceId, nextMode] = button.dataset.providerMode.split(":");
    state.providerModeByInstance[instanceId] = nextMode;
    if (nextMode !== "mcp") {
      state.mcpCreateOpenByInstance[instanceId] = false;
    }
    renderSelectedInstanceProviders(instance);
  }));
  target.querySelectorAll("[data-provider-focus]").forEach((button) => button.addEventListener("click", () => {
    const [instanceId, providerName] = button.dataset.providerFocus.split(":");
    state.providerFocusByInstance[instanceId] = providerName;
    renderSelectedInstanceProviders(instance);
  }));
  target.querySelectorAll("[data-mcp-focus]").forEach((button) => button.addEventListener("click", () => {
    const [instanceId, serverName] = button.dataset.mcpFocus.split(":");
    state.mcpFocusByInstance[instanceId] = serverName;
    state.mcpCreateOpenByInstance[instanceId] = false;
    renderSelectedInstanceProviders(instance);
  }));
  target.querySelectorAll("[data-mcp-create-toggle]").forEach((button) => button.addEventListener("click", () => {
    const instanceId = button.dataset.mcpCreateToggle;
    state.mcpCreateOpenByInstance[instanceId] = !state.mcpCreateOpenByInstance[instanceId];
    renderSelectedInstanceProviders(instance);
  }));
  target.querySelectorAll("[data-mcp-create-cancel]").forEach((button) => button.addEventListener("click", () => {
    const instanceId = button.dataset.mcpCreateCancel;
    state.mcpCreateOpenByInstance[instanceId] = false;
    renderSelectedInstanceProviders(instance);
  }));
  target.querySelectorAll("[data-provider-default-save]").forEach((button) => button.addEventListener("click", () => handleProviderDefaultsSave(button.dataset.providerDefaultSave)));
  target.querySelectorAll("[data-provider-save]").forEach((button) => button.addEventListener("click", () => handleProviderSave(button.dataset.providerSave)));
  target.querySelectorAll("[data-provider-validate]").forEach((button) => button.addEventListener("click", () => handleProviderValidate(button.dataset.providerValidate)));
  target.querySelectorAll("[data-mcp-save]").forEach((button) => button.addEventListener("click", () => handleMcpSave(button.dataset.mcpSave)));
  target.querySelectorAll("[data-mcp-validate]").forEach((button) => button.addEventListener("click", () => handleMcpValidate(button.dataset.mcpValidate)));
  target.querySelectorAll("[data-mcp-delete]").forEach((button) => button.addEventListener("click", () => handleMcpDelete(button.dataset.mcpDelete)));
  target.querySelectorAll("[data-mcp-create-save]").forEach((button) => button.addEventListener("click", () => handleMcpCreate(button.dataset.mcpCreateSave)));
}

function renderSelectedInstanceMemory(instance) {
  const target = document.getElementById("instance-workspace-memory");
  if (!target || !instance) return;
  const memoryState = getMemoryState(instance.id);
  const viewMode = state.memoryViewModeByInstance[instance.id] || "edit";
  const files = Object.values(memoryState.files || {});
  const selectedPath = memoryState.selectedPath || "AGENTS.md";
  const selectedFile = memoryState.files[selectedPath];

  if (!files.length && !memoryState.loading) {
    void loadInstanceMemoryFiles(instance.id);
  }

  const nav = files.length
    ? files
      .map((file) => {
        const dirty = file.content !== file.originalContent;
        return `
          <button class="console-tab ${file.path === selectedPath ? "is-active" : ""}" data-memory-file="${escapeHtml(instance.id)}:${escapeHtml(file.path)}">
            ${escapeHtml(file.path)}${dirty ? " *" : ""}
          </button>
        `;
      })
      .join("")
    : `<p class="meta">${memoryState.loading ? "Loading memory files..." : "No editable files found."}</p>`;

  const editorBody = selectedFile
    ? (() => {
      const dirty = selectedFile.content !== selectedFile.originalContent;
      const busy = state.busyKey === `memory-save:${instance.id}:${selectedFile.path}` ? "disabled" : "";
      if (viewMode === "preview") {
        return `
          <div class="item-card">
            <div class="row-between">
              <h4>Preview · ${escapeHtml(selectedFile.path)}</h4>
              <span class="badge ${badgeClass(selectedFile.exists ? "info" : "warning")}">${selectedFile.exists ? "File exists" : "Will be created on save"}</span>
            </div>
            <pre class="memory-preview">${escapeHtml(selectedFile.content || "(empty)")}</pre>
          </div>
        `;
      }
      return `
        <div class="item-card">
          <div class="row-between">
            <h4>Edit · ${escapeHtml(selectedFile.path)}</h4>
            <span class="badge ${badgeClass(dirty ? "warning" : "ok")}">${dirty ? "Unsaved" : "Saved"}</span>
          </div>
          <div class="field">
            <label for="memory-editor-${escapeHtml(instance.id)}">Content</label>
            <textarea id="memory-editor-${escapeHtml(instance.id)}" class="memory-editor-textarea" data-memory-editor="${escapeHtml(instance.id)}:${escapeHtml(selectedFile.path)}" ${busy}>${escapeHtml(selectedFile.content)}</textarea>
          </div>
          <div class="inline-actions">
            <button class="primary-button is-small" data-memory-save="${escapeHtml(instance.id)}:${escapeHtml(selectedFile.path)}" ${busy || !dirty ? "disabled" : ""}>Save</button>
            <button class="secondary-button is-small" data-memory-reset="${escapeHtml(instance.id)}:${escapeHtml(selectedFile.path)}" ${busy || !dirty ? "disabled" : ""}>Reset</button>
            <button class="secondary-button is-small" data-memory-reload="${escapeHtml(instance.id)}" ${busy}>Reload files</button>
          </div>
        </div>
      `;
    })()
    : `<div class="item-card"><h4>No file selected</h4><p class="meta">${memoryState.loading ? "Loading..." : "Choose a file from the list."}</p></div>`;

  target.innerHTML = `
    <div class="memory-workspace">
      <div class="memory-nav item-card">
        <div class="row-between">
          <h4>Workspace Files</h4>
          <button class="secondary-button is-small" data-memory-reload="${escapeHtml(instance.id)}" ${memoryState.loading ? "disabled" : ""}>Refresh</button>
        </div>
        <p class="meta">Preview and edit agent memory/prompt markdown files.</p>
        <div class="memory-file-list">${nav}</div>
      </div>
      <div class="stack">
        <div class="inline-actions">
          <button class="secondary-button is-small ${viewMode === "preview" ? "is-active" : ""}" data-memory-view="${escapeHtml(instance.id)}:preview">Preview</button>
          <button class="secondary-button is-small ${viewMode === "edit" ? "is-active" : ""}" data-memory-view="${escapeHtml(instance.id)}:edit">Edit</button>
        </div>
        ${editorBody}
      </div>
    </div>
  `;

  target.querySelectorAll("[data-memory-file]").forEach((button) => button.addEventListener("click", () => {
    const [instanceId, path] = button.dataset.memoryFile.split(":");
    handleMemoryFileSelect(instanceId, path);
  }));
  target.querySelectorAll("[data-memory-view]").forEach((button) => button.addEventListener("click", () => {
    const [instanceId, mode] = button.dataset.memoryView.split(":");
    state.memoryViewModeByInstance[instanceId] = mode === "preview" ? "preview" : "edit";
    renderSelectedInstanceMemory(selectedInstance());
  }));
  target.querySelectorAll("[data-memory-editor]").forEach((textarea) => textarea.addEventListener("input", (event) => {
    const [instanceId, path] = event.target.dataset.memoryEditor.split(":");
    handleMemoryEditorInput(instanceId, path, event.target.value);
  }));
  target.querySelectorAll("[data-memory-save]").forEach((button) => button.addEventListener("click", () => {
    const [instanceId, path] = button.dataset.memorySave.split(":");
    void handleMemoryFileSave(instanceId, path);
  }));
  target.querySelectorAll("[data-memory-reset]").forEach((button) => button.addEventListener("click", () => {
    const [instanceId, path] = button.dataset.memoryReset.split(":");
    handleMemoryFileReset(instanceId, path);
  }));
  target.querySelectorAll("[data-memory-reload]").forEach((button) => button.addEventListener("click", () => {
    const instanceId = button.dataset.memoryReload;
    void loadInstanceMemoryFiles(instanceId, { force: true });
  }));
}

function renderSelectedInstanceSchedules(instance) {
  const target = document.getElementById("instance-workspace-schedules");
  if (!target) return;
  const scheduleBucket = (state.schedules?.instances || []).find((item) => item.instance_id === instance.id);
  const jobs = (scheduleBucket?.jobs || [])
    .map((job) => {
      const toggleKey = `schedule-toggle:${instance.id}:${job.id}`;
      const runKey = `schedule-run:${instance.id}:${job.id}`;
      const deleteKey = `schedule-delete:${instance.id}:${job.id}`;
      const scheduleLabel =
        job.schedule.kind === "every"
          ? `Every ${job.schedule.every_ms || 0} ms`
          : job.schedule.kind === "cron"
            ? `Cron: ${job.schedule.expr || ""}${job.schedule.tz ? ` (${job.schedule.tz})` : ""}`
            : `At ${job.schedule.at_ms || ""}`;
      return `
        <div class="item-card">
          <div class="row-between">
            <h4>${escapeHtml(job.name)}</h4>
            <span class="badge ${badgeClass(job.enabled ? "ok" : "neutral")}">${job.enabled ? "Enabled" : "Disabled"}</span>
          </div>
          <p class="meta">${escapeHtml(scheduleLabel)}</p>
          <div class="inline-actions">
            <button class="secondary-button is-small" data-schedule-toggle="${escapeHtml(toggleKey)}">${job.enabled ? "Disable" : "Enable"}</button>
            <button class="primary-button is-small" data-schedule-run="${escapeHtml(runKey)}">Run Now</button>
            <button class="secondary-button is-small" data-schedule-delete="${escapeHtml(deleteKey)}">Delete</button>
          </div>
        </div>
      `;
    })
    .join("");
  target.innerHTML = `<div class="stack">${jobs || `<div class="item-card"><p class="meta">No schedules for this instance.</p></div>`}</div>`;
  target.querySelectorAll("[data-schedule-toggle]").forEach((button) => button.addEventListener("click", () => handleScheduleToggle(button.dataset.scheduleToggle)));
  target.querySelectorAll("[data-schedule-run]").forEach((button) => button.addEventListener("click", () => handleScheduleRun(button.dataset.scheduleRun)));
  target.querySelectorAll("[data-schedule-delete]").forEach((button) => button.addEventListener("click", () => handleScheduleDelete(button.dataset.scheduleDelete)));
}

function ensureRuntimeAuditState(instanceId) {
  if (state.runtimeAudit.instanceId === instanceId) {
    return;
  }
  if (state.runtimeAudit.debounceId) {
    window.clearTimeout(state.runtimeAudit.debounceId);
  }
  state.runtimeAudit = {
    ...state.runtimeAudit,
    instanceId,
    events: [],
    summary: null,
    filteredCount: 0,
    nextCursor: null,
    initialized: false,
    loading: false,
    loadingMore: false,
    debounceId: null,
    executionMode: "live",
    executionPinnedTraceId: "",
    executionLatestTraceId: "",
    executionUnreadTraceCount: 0,
    executionZoom: 1,
    executionFitToWindow: false,
  };
}

function runtimeAuditQuery(instanceId, cursor = null, options = {}) {
  const statusValue = options.status ?? state.runtimeAudit.status ?? "all";
  const operationValue = options.operation ?? state.runtimeAudit.operation ?? "all";
  const searchValue = options.search ?? state.runtimeAudit.search ?? "";
  const limitValue = Number(options.limit || 30);
  const params = new URLSearchParams();
  params.set("instance_id", instanceId);
  params.set("limit", String(Math.max(10, Math.min(limitValue, 200))));
  params.set("status", String(statusValue || "all"));
  params.set("operation", String(operationValue || "all"));
  if (String(searchValue || "").trim()) {
    params.set("search", String(searchValue).trim());
  }
  if (cursor !== null && cursor !== undefined && cursor !== "") {
    params.set("cursor", String(cursor));
  }
  params.set("_ts", String(Date.now()));
  return `/admin/runtime-audit?${params.toString()}`;
}

function runtimeAuditStatusClass(status) {
  return badgeClass(status === "error" ? "warning" : "info");
}

function runtimeOperationLabel(operation) {
  const value = String(operation || "").trim();
  if (value === "message_received") return "User Request";
  if (value === "message_completed") return "Result Delivered";
  if (value === "tool_start") return "Tool Started";
  if (value === "package_install") return "Package Install";
  if (value === "file_read") return "Read File";
  if (value === "file_write") return "Write File";
  if (value === "file_edit") return "Edit File";
  if (value === "file_list") return "List Files";
  if (value === "command") return "Shell Command";
  return value || "Tool Call";
}

function runtimeOperationLane(operation) {
  const value = String(operation || "").trim();
  if (value === "message_received") return "input";
  if (value === "message_completed") return "output";
  if (value === "tool_start") return "tool";
  if (value.startsWith("file_")) return "file";
  if (value === "command" || value === "package_install") return "tool";
  return "tool";
}

function executionTraceEventSignature(event) {
  if (!event) {
    return "";
  }
  const operation = String(event.operation || "").trim();
  return [
    operation,
    String(event.tool_name || "").trim(),
    String(event.command || "").trim(),
    String(event.path || "").trim(),
    String(event.package_manager || "").trim(),
  ].join("::");
}

function compactExecutionTraceEvents(events) {
  if (!Array.isArray(events) || events.length === 0) {
    return [];
  }
  const hiddenIndexes = new Set();
  const pendingStarts = new Map();
  events.forEach((event, index) => {
    const status = String(event?.status || "").trim().toLowerCase();
    const operation = String(event?.operation || "").trim();
    const eventType = String(event?.event_type || "").trim();
    const signature = executionTraceEventSignature(event);
    const isToolStart = eventType === "tool.start" || operation === "tool_start";
    if (isToolStart && status === "running" && signature) {
      const queued = pendingStarts.get(signature) || [];
      queued.push(index);
      pendingStarts.set(signature, queued);
      return;
    }
    if ((operation === "command" || operation === "package_install" || operation.startsWith("file_")) && signature) {
      const queued = pendingStarts.get(signature) || [];
      if (queued.length > 0) {
        hiddenIndexes.add(queued.shift());
        if (queued.length > 0) {
          pendingStarts.set(signature, queued);
        } else {
          pendingStarts.delete(signature);
        }
      }
    }
  });
  return events.filter((_, index) => !hiddenIndexes.has(index));
}

function buildExecutionTraces(events) {
  if (!Array.isArray(events) || events.length === 0) {
    return [];
  }
  const ordered = [...events].sort((left, right) => {
    const lts = parseTimestamp(left.ts) || 0;
    const rts = parseTimestamp(right.ts) || 0;
    if (lts !== rts) {
      return lts - rts;
    }
    return (Number(left.line) || 0) - (Number(right.line) || 0);
  });
  const traces = [];
  let bucket = [];
  let lastTs = null;
  for (const event of ordered) {
    const currentTs = parseTimestamp(event.ts);
    const operation = String(event.operation || "");
    const lastOperation = bucket.length > 0 ? String(bucket[bucket.length - 1].operation || "") : "";
    const sessionKey = String(event.session_key || "");
    const lastSessionKey = bucket.length > 0 ? String(bucket[bucket.length - 1].session_key || "") : "";
    const splitByNewMessage = bucket.length > 0 && operation === "message_received";
    const splitBySession =
      bucket.length > 0
      && sessionKey
      && lastSessionKey
      && sessionKey !== lastSessionKey;
    const splitByTime =
      bucket.length > 0
      && currentTs !== null
      && lastTs !== null
      && currentTs - lastTs > 90000
      && (lastOperation === "message_completed" || operation === "message_received" || !splitBySession);
    if (splitByNewMessage || splitBySession || splitByTime) {
      traces.push(bucket);
      bucket = [];
    }
    bucket.push(event);
    lastTs = currentTs;
  }
  if (bucket.length > 0) {
    traces.push(bucket);
  }
  return traces
    .map((trace, idx) => {
      const compactedEvents = compactExecutionTraceEvents(trace);
      const activeEvents = compactedEvents.length > 0 ? compactedEvents : trace;
      const first = activeEvents[0];
      const last = activeEvents[activeEvents.length - 1];
      const firstLine = Number(first?.line) || idx;
      const lastLine = Number(last?.line) || idx;
      return {
        id: `trace-${firstLine}-${lastLine}-${activeEvents.length}`,
        events: activeEvents,
        startedAt: first?.ts || "",
        endedAt: last?.ts || "",
      };
    })
    .sort((left, right) => (parseTimestamp(right.endedAt) || 0) - (parseTimestamp(left.endedAt) || 0));
}

function autoScrollExecutionGraphToLatest() {
  const container = document.querySelector(".execution-visualize-graph-scroll");
  if (!container) return;
  window.requestAnimationFrame(() => {
    container.scrollLeft = Math.max(0, container.scrollWidth - container.clientWidth);
  });
}

function summarizeTraceStatus(trace) {
  if (!trace || !trace.events?.length) {
    return "idle";
  }
  if (trace.events.some((event) => String(event.status || "") === "error")) {
    return "error";
  }
  if (trace.events.some((event) => String(event.operation || "") === "message_completed")) {
    return "ok";
  }
  const lastEvent = trace.events[trace.events.length - 1] || null;
  if (!lastEvent) {
    return "idle";
  }
  if (trace.events.some((event) => String(event.status || "") === "running")) {
    return "running";
  }
  return String(lastEvent.operation || "") === "message_completed" ? "ok" : "running";
}

function renderExecutionGraphSvg(trace, options = {}) {
  const zoom = clampExecutionZoom(options.zoom ?? 1);
  const fitToWindow = !!options.fitToWindow;
  if (!trace || !trace.events?.length) {
    return `<div class="item-card"><p class="meta">No execution trace available yet. Trigger an agent task to see the graph.</p></div>`;
  }
  const errorReasonFromEvent = (event) => {
    if (String(event?.status || "") !== "error") {
      return "";
    }
    const raw = String(event?.result_preview || event?.message_preview || "").trim();
    if (!raw) {
      return "Unknown error";
    }
    const firstLine = raw.split(/\r?\n/).find((line) => line.trim()) || raw;
    return firstLine.replace(/^Error:\s*/i, "").trim() || "Unknown error";
  };
  const laneY = {
    input: 82,
    tool: 176,
    file: 270,
    output: 364,
  };
  const hasExplicitStart = trace.events.some((event) => String(event.operation || "") === "message_received");
  const hasExplicitFinish = trace.events.some((event) => String(event.operation || "") === "message_completed");
  const traceStatus = summarizeTraceStatus(trace);
  const lastErrorEvent = [...trace.events].reverse().find((event) => String(event.status || "") === "error") || null;
  const traceErrorReason = errorReasonFromEvent(lastErrorEvent);
  const syntheticNodes = [
    ...(!hasExplicitStart ? [{
      id: "start",
      label: "User Request",
      detail: formatDateTime(trace.startedAt),
      lane: "input",
      status: "ok",
      kind: "synthetic",
    }] : []),
    ...trace.events.map((event, idx) => ({
      id: event.event_id || `event-${idx}`,
      label: runtimeOperationLabel(event.operation),
      detail:
        errorReasonFromEvent(event)
        || event.message_preview
        || event.command
        || event.path
        || event.tool_name
        || "event",
      lane: runtimeOperationLane(event.operation),
      status: event.status || "ok",
      kind: "event",
      ts: event.ts,
      resultPreview: event.result_preview || "",
      hasResult: !!(event.result_preview && String(event.result_preview).trim()),
    })),
    ...(!hasExplicitFinish ? [{
      id: "finish",
      label: traceStatus === "error" ? "Blocked / Error" : traceStatus === "running" ? "Running" : "Result Delivered",
      detail:
        traceStatus === "running"
          ? "Waiting for completion"
          : traceStatus === "error"
            ? (traceErrorReason || "Execution failed")
            : formatDateTime(trace.endedAt),
      lane: "output",
      status: traceStatus === "running" ? "running" : traceStatus,
      kind: "synthetic",
      resultPreview: (() => {
        // Find the last tool execution event (not session event) with actual result
        const toolEvents = trace.events.filter(e => 
          e.operation !== "message_received" && 
          e.operation !== "message_completed" &&
          e.result_preview && 
          String(e.result_preview).trim()
        );
        return toolEvents.length > 0 ? (toolEvents[toolEvents.length - 1].result_preview || "") : "";
      })(),
      hasResult: (() => {
        const toolEvents = trace.events.filter(e => 
          e.operation !== "message_received" && 
          e.operation !== "message_completed" &&
          e.result_preview && 
          String(e.result_preview).trim()
        );
        return toolEvents.length > 0;
      })(),
    }] : []),
  ];
  const stepX = 168;
  const marginX = 76;
  const width = Math.max(980, marginX * 2 + stepX * Math.max(syntheticNodes.length - 1, 1));
  const height = 430;
  const nodeWidth = 140;
  const nodeHeight = 60;
  const nodes = syntheticNodes.map((node, index) => {
    const x = marginX + index * stepX;
    const y = laneY[node.lane] || laneY.tool;
    return { ...node, x, y };
  });
  const edges = nodes.slice(1).map((node, index) => ({ from: nodes[index], to: node }));
  const laneLabels = [
    { lane: "input", text: "Request" },
    { lane: "tool", text: "Tool Execution" },
    { lane: "file", text: "Workspace I/O" },
    { lane: "output", text: "Response" },
  ];
  const trunc = (value, max) => {
    const text = String(value || "");
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
  };
  return `
    <div class="execution-visualize-canvas">
      <div class="execution-visualize-graph-scroll">
      <svg
        class="execution-visualize-graph ${fitToWindow ? "is-fit" : "is-manual"}"
        style="${fitToWindow ? "" : `width:${Math.round(width * zoom)}px;height:${Math.round(height * zoom)}px;`}"
        data-base-width="${width}"
        data-base-height="${height}"
        viewBox="0 0 ${width} ${height}"
        preserveAspectRatio="xMinYMin meet"
        role="img"
        aria-label="Execution flow graph"
      >
        <defs>
          <marker id="execution-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3.5" orient="auto">
            <polygon points="0 0, 8 3.5, 0 7" fill="#8aa2bb"></polygon>
          </marker>
        </defs>
        ${laneLabels
          .map((lane) => `
            <line x1="16" y1="${laneY[lane.lane]}" x2="${width - 16}" y2="${laneY[lane.lane]}" class="execution-lane-line"></line>
            <text x="16" y="${laneY[lane.lane] - 10}" class="execution-lane-label">${escapeHtml(lane.text)}</text>
          `)
          .join("")}
        ${edges
          .map((edge) => `
            <line
              x1="${edge.from.x + nodeWidth / 2}"
              y1="${edge.from.y + nodeHeight / 2}"
              x2="${edge.to.x - nodeWidth / 2}"
              y2="${edge.to.y + nodeHeight / 2}"
              class="execution-edge ${edge.to.status === "running" ? "is-running" : ""}"
              marker-end="url(#execution-arrow)"
            ></line>
          `)
          .join("")}
        ${nodes
          .map((node) => `
            <g transform="translate(${node.x - nodeWidth / 2}, ${node.y})">
              <rect class="execution-node ${node.status === "error" ? "is-error" : node.status === "running" ? "is-running" : "is-ok"} ${node.kind === "synthetic" ? "is-synthetic" : ""}" width="${nodeWidth}" height="${nodeHeight}" rx="12" ry="12"></rect>
              ${node.status === "running" ? `
                <g class="execution-node-spinner" transform="translate(112, 10)" aria-label="running">
                  <circle class="execution-node-spinner-track" cx="8" cy="8" r="6"></circle>
                  <circle class="execution-node-spinner-ring" cx="8" cy="8" r="6"></circle>
                </g>
              ` : ""}
              <text x="12" y="24" class="execution-node-title">${escapeHtml(trunc(node.label, 20))}</text>
              <text x="12" y="43" class="execution-node-detail">${escapeHtml(trunc(node.detail, 26))}</text>
              ${node.hasResult ? `
                <g class="execution-node-info" transform="translate(116, 46)" style="cursor: help;">
                  <circle cx="8" cy="8" r="7" fill="#4a90e2" opacity="0.9">
                    <title>Result Preview:
${escapeHtml(String(node.resultPreview || "(empty)").substring(0, 500))}</title>
                  </circle>
                  <text x="8" y="12" text-anchor="middle" fill="white" font-size="11" font-weight="bold" pointer-events="none">i</text>
                </g>
              ` : ""}
            </g>
          `)
          .join("")}
      </svg>
      </div>
    </div>
  `;
}

function renderRuntimeAuditExplorer(instance) {
  const events = state.runtimeAudit.instanceId === instance.id ? state.runtimeAudit.events : [];
  const filterStatus = state.runtimeAudit.status || "all";
  const filterOperation = state.runtimeAudit.operation || "all";
  const filterSearch = state.runtimeAudit.search || "";
  const isLoading = state.runtimeAudit.loading;
  const isLoadingMore = state.runtimeAudit.loadingMore;
  const operations = new Set(["all", "command", "package_install", "file_read", "file_write", "file_edit", "file_list"]);
  events.forEach((event) => {
    const operation = String(event.operation || "").trim();
    if (operation) {
      operations.add(operation);
    }
  });
  const operationOptions = Array.from(operations)
    .map((value) => `<option value="${escapeHtml(value)}" ${filterOperation === value ? "selected" : ""}>${escapeHtml(value)}</option>`)
    .join("");
  const rows = events.length
    ? events.map((event) => {
      const op = event.operation || "unknown";
      const isSessionMsg = event.tool_name === "session";
      const isToolInvocation = isSessionMsg && op === "message_completed" &&
        event.result_preview && !/[\n\r]/.test(event.result_preview) && event.result_preview.length < 300;
      const opBadgeClass = op === "package_install" ? "is-orange"
        : op === "web_fetch" || op === "web_search" ? "is-blue"
        : op === "message_received" ? "is-lime"
        : op === "message_completed" ? "is-gray"
        : "is-blue";
      const pathLabel = op === "web_fetch" ? "URL" : op === "web_search" ? "Query" : "Path";
      let bodyHtml = "";
      if (event.command) {
        bodyHtml += `<div class="runtime-audit-command"><code>${escapeHtml(event.command)}</code></div>`;
      }
      if (event.path) {
        bodyHtml += `<p class="meta"><strong style="font-weight:600">${escapeHtml(pathLabel)}:</strong> <span style="word-break:break-all">${escapeHtml(event.path)}</span></p>`;
      }
      if (event.result_preview) {
        if (isToolInvocation) {
          const calls = event.result_preview.split(/,\s*/);
          const badges = calls.map((c) => `<span class="badge is-blue" style="font-size:11px">${escapeHtml(c.trim())}</span>`).join(" ");
          bodyHtml += `<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap"><span class="meta" style="font-size:12px">Called:</span>${badges}</div>`;
        } else {
          bodyHtml += `<p class="meta" style="white-space:pre-wrap;word-break:break-word">${escapeHtml(event.result_preview)}</p>`;
        }
      }
      return `
      <article class="runtime-audit-row">
        <div class="row-between">
          <div class="event-meta">
            <span class="badge ${opBadgeClass}">${escapeHtml(op)}</span>
            <span class="badge ${runtimeAuditStatusClass(event.status)}">${escapeHtml(event.status || "ok")}</span>
            ${!isSessionMsg ? `<span>${escapeHtml(event.tool_name || "")}</span>` : ""}
            ${event.package_manager ? `<span>${escapeHtml(event.package_manager)}</span>` : ""}
            ${event.channel ? `<span class="meta" style="font-size:12px">${escapeHtml(event.channel)}</span>` : ""}
          </div>
          <span class="meta">${escapeHtml(formatDateTime(event.ts))}</span>
        </div>
        ${bodyHtml}
      </article>
    `;
    }).join("")
    : `<div class="item-card"><p class="meta">No runtime audit events match current filters.</p></div>`;

  return `
    <div class="runtime-audit-explorer">
      <div class="row-between">
        <div>
          <h4>Runtime Audit Explorer</h4>
          <p class="meta">Near real-time execution events for this instance only.</p>
        </div>
        <span class="badge ${state.runtimeAudit.autoRefresh ? badgeClass("ok") : badgeClass("info")}">
          ${state.runtimeAudit.autoRefresh ? "Auto-refresh on" : "Auto-refresh off"}
        </span>
      </div>
      <div class="runtime-audit-toolbar">
        <label class="field">
          <span>Status</span>
          <select id="runtime-audit-status">
            <option value="all" ${filterStatus === "all" ? "selected" : ""}>all</option>
            <option value="ok" ${filterStatus === "ok" ? "selected" : ""}>ok</option>
            <option value="error" ${filterStatus === "error" ? "selected" : ""}>error</option>
          </select>
        </label>
        <label class="field">
          <span>Operation</span>
          <select id="runtime-audit-operation">${operationOptions}</select>
        </label>
        <label class="field runtime-audit-search">
          <span>Search</span>
          <input id="runtime-audit-search" value="${escapeHtml(filterSearch)}" placeholder="command, path, output..." />
        </label>
        <div class="inline-actions runtime-audit-actions">
          <label class="runtime-audit-checkbox">
            <input id="runtime-audit-auto-refresh" type="checkbox" ${state.runtimeAudit.autoRefresh ? "checked" : ""}>
            Auto
          </label>
          <button id="runtime-audit-refresh" class="secondary-button is-small" ${isLoading ? "disabled" : ""}>Refresh</button>
        </div>
      </div>
      <div class="runtime-audit-list">${rows}</div>
      <div class="runtime-audit-footer">
        <span class="meta">${escapeHtml(formatNumber(state.runtimeAudit.filteredCount || 0))} filtered events</span>
        ${state.runtimeAudit.nextCursor !== null ? `<button id="runtime-audit-load-more" class="secondary-button is-small" ${isLoadingMore ? "disabled" : ""}>Load more</button>` : ""}
      </div>
    </div>
  `;
}

function bindRuntimeAuditExplorer(instanceId) {
  document.getElementById("runtime-audit-status")?.addEventListener("change", (event) => {
    state.runtimeAudit.status = event.target.value || "all";
    state.runtimeAudit.nextCursor = null;
    void refreshRuntimeAuditExplorer({ append: false, silent: true });
  });
  document.getElementById("runtime-audit-operation")?.addEventListener("change", (event) => {
    state.runtimeAudit.operation = event.target.value || "all";
    state.runtimeAudit.nextCursor = null;
    void refreshRuntimeAuditExplorer({ append: false, silent: true });
  });
  document.getElementById("runtime-audit-search")?.addEventListener("input", (event) => {
    state.runtimeAudit.search = event.target.value || "";
    state.runtimeAudit.nextCursor = null;
    if (state.runtimeAudit.debounceId) {
      window.clearTimeout(state.runtimeAudit.debounceId);
    }
    state.runtimeAudit.debounceId = window.setTimeout(() => {
      state.runtimeAudit.debounceId = null;
      void refreshRuntimeAuditExplorer({ append: false, silent: true });
    }, 350);
  });
  document.getElementById("runtime-audit-auto-refresh")?.addEventListener("change", (event) => {
    state.runtimeAudit.autoRefresh = !!event.target.checked;
  });
  document.getElementById("runtime-audit-refresh")?.addEventListener("click", () => {
    void refreshRuntimeAuditExplorer({ append: false, silent: false });
  });
  document.getElementById("runtime-audit-load-more")?.addEventListener("click", () => {
    void refreshRuntimeAuditExplorer({ append: true, silent: true });
  });
  if (state.runtimeAudit.instanceId !== instanceId) {
    ensureRuntimeAuditState(instanceId);
    void refreshRuntimeAuditExplorer({ append: false, silent: true });
  } else if (!state.runtimeAudit.loading && !state.runtimeAudit.initialized) {
    void refreshRuntimeAuditExplorer({ append: false, silent: true });
  }
}

async function refreshRuntimeAuditExplorer({ append = false, silent = true } = {}) {
  const instance = selectedInstance();
  const runtimeTabActive = state.instanceWorkspaceTab === "runtime-audit" || state.instanceWorkspaceTab === "execution-visualize";
  if (!instance || !runtimeTabActive || state.currentView !== "instances") {
    return;
  }
  ensureRuntimeAuditState(instance.id);
  if (append) {
    if (state.runtimeAudit.loadingMore || state.runtimeAudit.nextCursor === null) {
      return;
    }
    state.runtimeAudit.loadingMore = true;
  } else {
    if (state.runtimeAudit.loading) {
      return;
    }
    state.runtimeAudit.loading = true;
  }
  if (state.instanceWorkspaceTab === "execution-visualize") {
    renderSelectedInstanceExecutionVisualize(instance);
  } else {
    renderSelectedInstanceRuntimeAudit(instance);
  }
  try {
    const cursor = append ? state.runtimeAudit.nextCursor : null;
    const inExecutionVisualize = state.instanceWorkspaceTab === "execution-visualize";
    const payload = await fetchJson(
      runtimeAuditQuery(
        instance.id,
        cursor,
        inExecutionVisualize
          ? { status: "all", operation: "all", search: "", limit: 120 }
          : { limit: 30 },
      ),
    );
    const incomingEvents = payload.events || [];
    state.runtimeAudit.summary = payload.summary || null;
    state.runtimeAudit.filteredCount = payload.filtered_count || 0;
    state.runtimeAudit.nextCursor = payload.next_cursor ?? null;
    state.runtimeAudit.initialized = true;
    state.runtimeAudit.events = append
      ? state.runtimeAudit.events.concat(incomingEvents)
      : incomingEvents;
    clearBanner();
  } catch (error) {
    if (!silent) {
      setBanner(`Unable to load runtime audit events: ${error.message}`, "error");
    }
  } finally {
    state.runtimeAudit.loading = false;
    state.runtimeAudit.loadingMore = false;
    const selected = selectedInstance();
    if (selected && selected.id === instance.id && state.currentView === "instances") {
      if (state.instanceWorkspaceTab === "execution-visualize") {
        renderSelectedInstanceExecutionVisualize(selected);
      } else if (state.instanceWorkspaceTab === "runtime-audit") {
        renderSelectedInstanceRuntimeAudit(selected);
      }
    }
  }
}

function renderSelectedInstanceSecurity(instance) {
  const target = document.getElementById("instance-workspace-security");
  if (!target) return;
  const findings = instance.security?.findings || [];
  const key = `restriction:${instance.id}`;
  const checked = findings.every((finding) => finding.code !== "workspace_restriction_disabled");
  const disabled = state.busyKey === key ? "disabled" : "";
  const sandboxProfile = normalizeSandboxProfile(instance.runtime_config?.sandbox?.profile || "balanced");
  const impact = runtimeImpactSummary({
    runtimeMode: instance.runtime_config?.mode || "host",
    sandboxExecutionStrategy: instance.runtime_config?.sandbox?.executionStrategy || "persistent",
    sandboxNetworkPolicy: instance.runtime_config?.sandbox?.networkPolicy || "default",
  });
  target.innerHTML = `
    <div class="stack">
      ${findings.map((finding) => `
        <div class="item-card">
          <div class="row-between">
            <h4>${escapeHtml(finding.title)}</h4>
            <span class="badge ${badgeClass(finding.severity)}">${escapeHtml(finding.severity)}</span>
          </div>
          <p class="meta">${escapeHtml(finding.detail)}</p>
        </div>
      `).join("") || `<div class="item-card"><p class="meta">No findings</p></div>`}
      <div class="item-card">
        <div class="row-between">
          <div>
            <h4>Runtime Posture</h4>
            <p class="meta">Runtime is configured from the Manage tab to avoid conflicting settings.</p>
          </div>
          <span class="badge ${badgeClass(sandboxProfile === "strict" ? "warning" : sandboxProfile === "fast" ? "info" : "ok")}">${escapeHtml(sandboxProfileLabel(sandboxProfile))}</span>
        </div>
        <div class="instance-summary-list">
          <div class="instance-summary-row">
            <span>Profile</span>
            <strong>${escapeHtml(sandboxProfileLabel(sandboxProfile))}</strong>
          </div>
          <div class="instance-summary-row">
            <span>Internet Access</span>
            <strong>${escapeHtml(impact.internet)}</strong>
          </div>
          <div class="instance-summary-row">
            <span>Tool Execution</span>
            <strong>${escapeHtml(impact.toolExecution)}</strong>
          </div>
        </div>
        <p class="meta">${escapeHtml(sandboxProfileSummary(sandboxProfile))}</p>
      </div>
      <div class="item-card">
        <h4>Workspace Restriction</h4>
        <div class="field">
          <label><input type="checkbox" data-restriction-toggle="${escapeHtml(instance.id)}" ${checked ? "checked" : ""} ${disabled}> Restrict tools to workspace</label>
        </div>
        <div class="inline-actions"><button class="primary-button is-small" data-restriction-save="${escapeHtml(instance.id)}" ${disabled}>Save</button></div>
      </div>
    </div>
  `;
  target.querySelectorAll("[data-restriction-save]").forEach((button) => button.addEventListener("click", () => handleRestrictionSave(button.dataset.restrictionSave)));
}

function renderSelectedInstanceRuntimeAudit(instance) {
  const target = document.getElementById("instance-workspace-runtime-audit");
  if (!target) return;
  ensureRuntimeAuditState(instance.id);
  const runtimeAudit = {
    ...(instance.runtime_audit || {}),
    ...(state.runtimeAudit.instanceId === instance.id && state.runtimeAudit.summary ? state.runtimeAudit.summary : {}),
  };
  target.innerHTML = `
    <div class="stack">
      <div class="item-card">
        <div class="row-between">
          <div>
            <h4>Runtime Audit</h4>
            <p class="meta">Execution-level audit for shell commands, file operations, package installs, and policy blocks.</p>
          </div>
          <span class="badge ${badgeClass((runtimeAudit.blocked_count || 0) > 0 ? "warning" : "info")}">${formatNumber(runtimeAudit.event_count || 0)} events</span>
        </div>
        <div class="instance-summary-list">
          <div class="instance-summary-row">
            <span>Shell Commands</span>
            <strong>${formatNumber(runtimeAudit.exec_count || 0)}</strong>
          </div>
          <div class="instance-summary-row">
            <span>File Operations</span>
            <strong>${formatNumber(runtimeAudit.file_op_count || 0)}</strong>
          </div>
          <div class="instance-summary-row">
            <span>Package Installs</span>
            <strong>${formatNumber(runtimeAudit.package_install_count || 0)}</strong>
          </div>
          <div class="instance-summary-row">
            <span>Blocked Commands</span>
            <strong>${formatNumber(runtimeAudit.blocked_count || 0)}</strong>
          </div>
          <div class="instance-summary-row">
            <span>Last Event</span>
            <strong>${escapeHtml(formatDateTime(runtimeAudit.last_event_at))}</strong>
          </div>
        </div>
        <p class="meta">${runtimeAudit.exists ? "Source: workspace/.nanobot/runtime-audit.jsonl" : "Runtime audit has not been initialized for this instance yet."}</p>
        ${renderRuntimeAuditExplorer(instance)}
      </div>
    </div>
  `;
  bindRuntimeAuditExplorer(instance.id);
}

function renderSelectedInstanceExecutionVisualize(instance) {
  const target = document.getElementById("instance-workspace-execution-visualize");
  if (!target) return;
  ensureRuntimeAuditState(instance.id);
  const events = state.runtimeAudit.instanceId === instance.id ? state.runtimeAudit.events : [];
  const traces = buildExecutionTraces(events);
  const latestTrace = traces[0] || null;
  if (latestTrace) {
    state.runtimeAudit.executionLatestTraceId = latestTrace.id;
  }
  if (state.runtimeAudit.executionMode !== "history") {
    state.runtimeAudit.executionMode = "live";
    state.runtimeAudit.executionPinnedTraceId = latestTrace?.id || "";
    state.runtimeAudit.executionUnreadTraceCount = 0;
  } else if (!state.runtimeAudit.executionPinnedTraceId && latestTrace) {
    state.runtimeAudit.executionPinnedTraceId = latestTrace.id;
  }
  let activeTrace = null;
  if (state.runtimeAudit.executionMode === "history") {
    activeTrace = traces.find((trace) => trace.id === state.runtimeAudit.executionPinnedTraceId) || latestTrace;
    state.runtimeAudit.executionPinnedTraceId = activeTrace?.id || "";
    const pinnedIndex = activeTrace ? traces.findIndex((trace) => trace.id === activeTrace.id) : -1;
    state.runtimeAudit.executionUnreadTraceCount = pinnedIndex > 0 ? pinnedIndex : 0;
  } else {
    activeTrace = latestTrace;
  }
  const traceOptions = traces
    .map((trace, idx) => {
      const summary = summarizeTraceStatus(trace);
      const selected = state.runtimeAudit.executionPinnedTraceId
        ? state.runtimeAudit.executionPinnedTraceId === trace.id
        : idx === 0;
      const title = `#${idx + 1} · ${trace.events.length} steps · ${formatDateTime(trace.startedAt)} → ${formatDateTime(trace.endedAt)}`;
      return `<option value="${escapeHtml(trace.id)}" ${selected ? "selected" : ""}>${escapeHtml(title)}${summary === "error" ? " · has error" : ""}${idx === 0 ? " · latest" : ""}</option>`;
    })
    .join("");
  const activeStatus = summarizeTraceStatus(activeTrace);
  const loading = state.runtimeAudit.loading;
  const isLiveMode = state.runtimeAudit.executionMode !== "history";
  const unread = state.runtimeAudit.executionUnreadTraceCount || 0;
  const executionZoom = clampExecutionZoom(state.runtimeAudit.executionZoom ?? 1);
  const fitToWindow = !!state.runtimeAudit.executionFitToWindow;
  target.innerHTML = `
    <div class="stack">
      <div class="item-card">
        <div class="row-between">
          <div>
            <h4>Execution Visualize</h4>
            <p class="meta">Follow live execution flow or pin a historical trace for investigation.</p>
          </div>
          <span class="badge ${activeStatus === "error" ? badgeClass("warning") : activeStatus === "running" ? badgeClass("info") : badgeClass("ok")}">${activeTrace ? (activeStatus === "error" ? "Trace has error" : activeStatus === "running" ? "Trace running" : "Trace healthy") : "Waiting for trace"}</span>
        </div>
        <div class="inline-actions execution-visualize-mode">
          <button id="execution-mode-live" class="secondary-button is-small ${isLiveMode ? "is-active" : ""}">Live Monitor</button>
          <button id="execution-mode-history" class="secondary-button is-small ${isLiveMode ? "" : "is-active"}">Trace History</button>
          ${!isLiveMode && unread > 0 ? `<span class="badge ${badgeClass("info")}">${escapeHtml(String(unread))} new traces</span>` : ""}
        </div>
        <div class="runtime-audit-toolbar execution-visualize-toolbar">
          ${!isLiveMode ? `<label class="field">
            <span>Trace</span>
            <select id="execution-visualize-trace">
              ${traceOptions || `<option value="">No trace yet</option>`}
            </select>
          </label>` : `
          <div class="field">
            <span>Live Feed</span>
            <p class="meta">Following latest trace automatically (no manual switching).</p>
          </div>`}
          <div class="inline-actions runtime-audit-actions">
            <label class="runtime-audit-checkbox">
              <input id="execution-visualize-auto-refresh" type="checkbox" ${state.runtimeAudit.autoRefresh ? "checked" : ""}>
              Auto
            </label>
            <div class="execution-zoom-controls">
              <button id="execution-visualize-zoom-out" class="secondary-button is-small" title="Zoom out">−</button>
              <span class="meta execution-zoom-level">${fitToWindow ? "Fit" : `${Math.round(executionZoom * 100)}%`}</span>
              <button id="execution-visualize-zoom-in" class="secondary-button is-small" title="Zoom in">+</button>
              <button id="execution-visualize-zoom-reset" class="secondary-button is-small">100%</button>
              <button id="execution-visualize-zoom-fit" class="secondary-button is-small ${fitToWindow ? "is-active" : ""}">Fit Window</button>
            </div>
            <button id="execution-visualize-refresh" class="secondary-button is-small" ${loading ? "disabled" : ""}>Refresh</button>
            ${!isLiveMode && unread > 0 ? `<button id="execution-visualize-jump-latest" class="primary-button is-small">Jump to latest</button>` : ""}
          </div>
        </div>
        ${!isLiveMode && unread > 0 ? `<p class="meta execution-visualize-hint">You are viewing historical trace. New executions are coming in the background.</p>` : ""}
        <p class="meta">${activeTrace ? `${activeTrace.events.length} events in selected trace.` : "No events yet. Once agent executes tools, graph will appear automatically."}</p>
        ${renderExecutionGraphSvg(activeTrace, { zoom: executionZoom, fitToWindow })}
      </div>
    </div>
  `;
  bindExecutionVisualize(instance.id);
  if (isLiveMode) {
    autoScrollExecutionGraphToLatest();
  }
}

function bindExecutionVisualize(instanceId) {
  document.getElementById("execution-mode-live")?.addEventListener("click", () => {
    state.runtimeAudit.executionMode = "live";
    state.runtimeAudit.executionUnreadTraceCount = 0;
    const selected = selectedInstance();
    if (selected && selected.id === instanceId) {
      renderSelectedInstanceExecutionVisualize(selected);
    }
  });
  document.getElementById("execution-mode-history")?.addEventListener("click", () => {
    state.runtimeAudit.executionMode = "history";
    if (!state.runtimeAudit.executionPinnedTraceId) {
      state.runtimeAudit.executionPinnedTraceId = state.runtimeAudit.executionLatestTraceId || "";
    }
    const selected = selectedInstance();
    if (selected && selected.id === instanceId) {
      renderSelectedInstanceExecutionVisualize(selected);
    }
  });
  document.getElementById("execution-visualize-trace")?.addEventListener("change", (event) => {
    state.runtimeAudit.executionMode = "history";
    state.runtimeAudit.executionPinnedTraceId = event.target.value || "";
    const selected = selectedInstance();
    if (selected && selected.id === instanceId) {
      renderSelectedInstanceExecutionVisualize(selected);
    }
  });
  document.getElementById("execution-visualize-auto-refresh")?.addEventListener("change", (event) => {
    state.runtimeAudit.autoRefresh = !!event.target.checked;
  });
  document.getElementById("execution-visualize-refresh")?.addEventListener("click", () => {
    void refreshRuntimeAuditExplorer({ append: false, silent: false });
  });
  document.getElementById("execution-visualize-zoom-in")?.addEventListener("click", () => {
    state.runtimeAudit.executionFitToWindow = false;
    state.runtimeAudit.executionZoom = clampExecutionZoom((state.runtimeAudit.executionZoom || 1) * EXECUTION_ZOOM_STEP);
    const selected = selectedInstance();
    if (selected && selected.id === instanceId) {
      renderSelectedInstanceExecutionVisualize(selected);
    }
  });
  document.getElementById("execution-visualize-zoom-out")?.addEventListener("click", () => {
    state.runtimeAudit.executionFitToWindow = false;
    state.runtimeAudit.executionZoom = clampExecutionZoom((state.runtimeAudit.executionZoom || 1) / EXECUTION_ZOOM_STEP);
    const selected = selectedInstance();
    if (selected && selected.id === instanceId) {
      renderSelectedInstanceExecutionVisualize(selected);
    }
  });
  document.getElementById("execution-visualize-zoom-reset")?.addEventListener("click", () => {
    state.runtimeAudit.executionFitToWindow = false;
    state.runtimeAudit.executionZoom = 1;
    const selected = selectedInstance();
    if (selected && selected.id === instanceId) {
      renderSelectedInstanceExecutionVisualize(selected);
    }
  });
  document.getElementById("execution-visualize-zoom-fit")?.addEventListener("click", () => {
    state.runtimeAudit.executionFitToWindow = true;
    const selected = selectedInstance();
    if (selected && selected.id === instanceId) {
      renderSelectedInstanceExecutionVisualize(selected);
    }
  });
  document.getElementById("execution-visualize-jump-latest")?.addEventListener("click", () => {
    state.runtimeAudit.executionMode = "live";
    state.runtimeAudit.executionUnreadTraceCount = 0;
    const selected = selectedInstance();
    if (selected && selected.id === instanceId) {
      renderSelectedInstanceExecutionVisualize(selected);
    }
  });
  if (state.runtimeAudit.instanceId !== instanceId) {
    ensureRuntimeAuditState(instanceId);
    void refreshRuntimeAuditExplorer({ append: false, silent: true });
  } else if (!state.runtimeAudit.loading && !state.runtimeAudit.initialized) {
    void refreshRuntimeAuditExplorer({ append: false, silent: true });
  }
}

function renderInstanceForm() {
  if (!state.instanceEditor) {
    state.instanceEditor = defaultInstanceEditor();
  }
  const editor = state.instanceEditor;
  const selected = selectedInstance();
  const derived = deriveInstanceValues(editor);
  const normalizedInstanceId = derived.instanceId.trim() || "<instance-id>";
  const softnixHome = inferSoftnixHome();
  const targetInstanceHome = `${softnixHome}/instances/${normalizedInstanceId}`;
  const targetConfigPath = `${targetInstanceHome}/config.json`;
  const duplicate = editor.mode === "create"
    ? state.overview.instances.some((instance) => instance.id === derived.instanceId)
    : false;
  const busy = state.busyKey === "instance-form" ? "disabled" : "";
  const modeLabel = editor.mode === "edit" ? `Edit ${editor.targetId}` : "Create New Instance";
  const runtimeAudit = selected?.runtime_audit || null;
  const showSandboxConfig = editor.runtimeMode === "sandbox" || editor.sandboxExecutionStrategy === "tool_ephemeral";
  const runtimeCustom = isCustomRuntimeConfig(editor);
  const showRuntimeOverrideControls = editor.mode === "edit" || editor.advanced;
  const impact = runtimeImpactSummary(editor);
  const target = document.getElementById("instance-workspace-manage");
  if (!target) return;
  if (!selected && editor.mode === "create" && !state.instanceCreateOpen) {
    target.innerHTML = `
      <div class="item-card">
        <h4>Instance Workspace</h4>
        <p class="meta">Select an instance from the table to manage it, or click <strong>Add Instance</strong> to create a new one.</p>
      </div>
    `;
    return;
  }
  target.innerHTML = `
    <div class="instance-form-shell">
    ${editor.mode === "create" ? `
    <div class="inline-actions">
      <button id="instance-form-simple" class="secondary-button is-small ${editor.advanced ? "" : "is-active"}" ${busy}>Simple</button>
      <button id="instance-form-advanced" class="secondary-button is-small ${editor.advanced ? "is-active" : ""}" ${busy}>Advanced</button>
    </div>` : ""}
    ${selected ? `
    <div class="instance-focus-banner">
      <div>
        <p class="eyebrow">Selected Instance</p>
        <h4>${escapeHtml(selected.name)}</h4>
        <p class="meta">${escapeHtml(selected.runtime.status)} · ${escapeHtml(selected.runtime.probe?.detail || selected.runtime.reason)}</p>
      </div>
      <span class="badge ${badgeClass(selected.runtime.status === "running" ? "ok" : selected.runtime.status === "stopped" ? "warning" : "info")}">${escapeHtml(selected.runtime.status)}</span>
    </div>` : ""}
    ${selected ? `
    <div class="instance-summary-card">
      <div class="row-between">
        <div>
          <p class="eyebrow">Runtime Audit</p>
          <h4>Recent execution footprint</h4>
        </div>
        <span class="badge ${badgeClass((runtimeAudit?.blocked_count || 0) > 0 ? "warning" : "info")}">${formatNumber(runtimeAudit?.event_count || 0)} events</span>
      </div>
      <div class="instance-summary-list">
        <div class="instance-summary-row">
          <span>Shell Commands</span>
          <strong>${formatNumber(runtimeAudit?.exec_count || 0)}</strong>
        </div>
        <div class="instance-summary-row">
          <span>File Operations</span>
          <strong>${formatNumber(runtimeAudit?.file_op_count || 0)}</strong>
        </div>
        <div class="instance-summary-row">
          <span>Package Installs</span>
          <strong>${formatNumber(runtimeAudit?.package_install_count || 0)}</strong>
        </div>
        <div class="instance-summary-row">
          <span>Blocked Commands</span>
          <strong>${formatNumber(runtimeAudit?.blocked_count || 0)}</strong>
        </div>
        <div class="instance-summary-row">
          <span>Last Event</span>
          <strong>${escapeHtml(formatDateTime(runtimeAudit?.last_event_at))}</strong>
        </div>
      </div>
      <p class="meta">${runtimeAudit?.exists ? "Source: workspace/.nanobot/runtime-audit.jsonl" : "Runtime audit has not been initialized for this instance yet."}</p>
    </div>` : ""}
    <div class="item-card">
      <div class="row-between">
        <h4>${escapeHtml(modeLabel)}</h4>
        ${editor.mode === "create" ? `<span class="badge ${badgeClass("info")}">${editor.advanced ? "Advanced" : "Simple"}</span>` : `<span class="badge ${badgeClass("warning")}">Edit</span>`}
      </div>
      <div class="field">
        <label for="instance-form-name">Name</label>
        <input id="instance-form-name" value="${escapeHtml(editor.name)}" ${busy}>
      </div>
      <div class="field">
        <label for="instance-form-env">Environment</label>
        <select id="instance-form-env" ${busy}>
          ${["prod", "uat", "staging", "dev"].map((value) => `<option value="${value}" ${derived.env === value ? "selected" : ""}>${value}</option>`).join("")}
        </select>
      </div>
      <div class="instance-summary-card">
        <div class="row-between">
          <div>
            <p class="eyebrow">Runtime Profile</p>
            <h4>${escapeHtml(sandboxProfileLabel(editor.sandboxProfile || "balanced"))}</h4>
          </div>
          <span class="badge ${runtimeCustom ? badgeClass("warning") : badgeClass("ok")}">${runtimeCustom ? "Custom Runtime" : "Profile Default"}</span>
        </div>
        <div class="field">
          <label for="instance-form-sandbox-profile">Runtime Profile</label>
          <select id="instance-form-sandbox-profile" ${busy}>
            <option value="balanced" ${editor.sandboxProfile === "balanced" ? "selected" : ""}>Connected</option>
            <option value="strict" ${editor.sandboxProfile === "strict" ? "selected" : ""}>Offline</option>
            <option value="fast" ${editor.sandboxProfile === "fast" ? "selected" : ""}>Max Capability</option>
          </select>
          <p class="meta">${escapeHtml(sandboxProfileSummary(editor.sandboxProfile || "balanced"))}</p>
        </div>
        <div class="instance-summary-list">
          <div class="instance-summary-row">
            <span>Internet Access</span>
            <strong>${escapeHtml(impact.internet)}</strong>
          </div>
          <div class="instance-summary-row">
            <span>Tool Execution</span>
            <strong>${escapeHtml(impact.toolExecution)}</strong>
          </div>
          <div class="instance-summary-row">
            <span>Runtime Engine</span>
            <strong>${escapeHtml(`${editor.runtimeMode || "sandbox"} / ${editor.sandboxExecutionStrategy || "persistent"}`)}</strong>
          </div>
        </div>
        ${showRuntimeOverrideControls ? `
        <div class="inline-actions">
          <button id="instance-runtime-override-toggle" class="secondary-button is-small ${editor.runtimeOverrideOpen ? "is-active" : ""}" ${busy}>${editor.runtimeOverrideOpen ? "Hide Advanced Runtime" : "Advanced Runtime Override"}</button>
          ${runtimeCustom ? `<button id="instance-runtime-reset" class="secondary-button is-small" ${busy}>Reset to Profile Defaults</button>` : ""}
        </div>` : ""}
      </div>
      <div class="instance-summary-card">
        <div class="row-between">
          <div>
            <p class="eyebrow">Generated Setup</p>
            <h4>Preview</h4>
          </div>
          <span class="badge ${duplicate ? badgeClass("error") : badgeClass("info")}">${duplicate ? "Conflict" : "Auto"}</span>
        </div>
        <div class="instance-summary-list">
          <div class="instance-summary-row">
            <span>Instance ID</span>
            <strong id="instance-preview-instance-id">${escapeHtml(derived.instanceId || "")}</strong>
          </div>
          <div class="instance-summary-row">
            <span>Owner</span>
            <strong id="instance-preview-owner">${escapeHtml(derived.owner || "")}</strong>
          </div>
          <div class="instance-summary-row">
            <span>Home</span>
            <strong id="instance-preview-home">${escapeHtml(targetInstanceHome)}</strong>
          </div>
          <div class="instance-summary-row">
            <span>Config</span>
            <strong id="instance-preview-config">${escapeHtml(targetConfigPath)}</strong>
          </div>
          <div class="instance-summary-row">
            <span>Gateway Port</span>
            <strong id="instance-preview-port">${escapeHtml((editor.gatewayPort || "").trim() || "Auto (next available)")}</strong>
          </div>
          <div class="instance-summary-row">
            <span>Runtime</span>
            <strong id="instance-preview-runtime">${escapeHtml(editor.runtimeMode || "sandbox")}</strong>
          </div>
          ${editor.mode === "create" ? `
          <div class="instance-summary-row">
            <span>Template</span>
            <strong id="instance-preview-template">${escapeHtml(derived.sourceConfig || "~/.nanobot/config.json (system default)")}</strong>
          </div>` : ""}
        </div>
        <p id="instance-preview-instance-id-note" class="meta"${duplicate ? ` style="color:#9a3e36"` : ""}>${duplicate ? "Instance ID already exists in registry." : "Generated automatically from Name + Environment."}</p>
      </div>
      ${editor.advanced || editor.mode === "edit" ? `
      <div class="instance-advanced-section">
        <p class="eyebrow">Advanced Settings</p>
        <div class="field">
          <label for="instance-form-id">Instance ID Override</label>
          <input id="instance-form-id" value="${escapeHtml(editor.instanceId)}" ${editor.mode === "edit" ? "disabled" : busy}>
        </div>
        <div class="field">
          <label for="instance-form-owner">Owner Override</label>
          <input id="instance-form-owner" value="${escapeHtml(editor.owner)}" ${busy}>
        </div>
        <div class="field">
          <label for="instance-form-repo-root">Repo Root</label>
          <input id="instance-form-repo-root" value="${escapeHtml(editor.repoRoot)}" ${busy}>
        </div>
        <div class="field">
          <label for="instance-form-nanobot-bin">nanobot Binary</label>
          <input id="instance-form-nanobot-bin" value="${escapeHtml(editor.nanobotBin)}" ${busy}>
        </div>
        <div class="field">
          <label for="instance-form-gateway-port">Gateway Port</label>
          <input id="instance-form-gateway-port" type="number" min="1" max="65535" value="${escapeHtml(editor.gatewayPort || "")}" placeholder="Auto assign if empty" ${busy}>
        </div>
        ${editor.runtimeOverrideOpen ? `
        <div class="instance-summary-card">
          <div class="row-between">
            <div>
              <p class="eyebrow">Expert Override</p>
              <h4>Custom Runtime Controls</h4>
            </div>
            <span class="badge ${badgeClass("warning")}">Advanced</span>
          </div>
          <p class="meta">Only change these values if the profile defaults are not enough. Incorrect combinations can reduce agent capability or break runtime behavior.</p>
          <div class="field">
            <label for="instance-form-runtime-mode">Runtime Mode</label>
            <select id="instance-form-runtime-mode" ${busy}>
              <option value="sandbox" ${editor.runtimeMode === "sandbox" ? "selected" : ""}>sandbox</option>
              <option value="host" ${editor.runtimeMode === "host" ? "selected" : ""}>host</option>
            </select>
          </div>
          <div class="field">
            <label for="instance-form-sandbox-strategy">Sandbox Execution Strategy</label>
            <select id="instance-form-sandbox-strategy" ${busy}>
              <option value="persistent" ${editor.sandboxExecutionStrategy === "persistent" ? "selected" : ""}>persistent</option>
              ${editor.runtimeMode !== "sandbox" ? `<option value="tool_ephemeral" ${editor.sandboxExecutionStrategy === "tool_ephemeral" ? "selected" : ""}>tool_ephemeral</option>` : ""}
            </select>
          </div>
          ${showSandboxConfig ? `
        <div class="field">
          <label for="instance-form-sandbox-image">Sandbox Image</label>
          <input id="instance-form-sandbox-image" value="${escapeHtml(editor.sandboxImage || "softnixclaw:latest")}" placeholder="softnixclaw:latest" ${busy}>
        </div>
        <div class="field">
          <label for="instance-form-sandbox-cpu">Sandbox CPU Limit</label>
          <input id="instance-form-sandbox-cpu" value="${escapeHtml(editor.sandboxCpuLimit || "")}" placeholder="1.0" ${busy}>
        </div>
        <div class="field">
          <label for="instance-form-sandbox-memory">Sandbox Memory Limit</label>
          <input id="instance-form-sandbox-memory" value="${escapeHtml(editor.sandboxMemoryLimit || "")}" placeholder="1g" ${busy}>
        </div>
        <div class="field">
          <label for="instance-form-sandbox-pids">Sandbox PIDs Limit</label>
          <input id="instance-form-sandbox-pids" type="number" min="1" value="${escapeHtml(editor.sandboxPidsLimit || "256")}" placeholder="256" ${busy}>
        </div>
        <div class="field">
          <label for="instance-form-sandbox-tmpfs">Sandbox tmpfs Size (MB)</label>
          <input id="instance-form-sandbox-tmpfs" type="number" min="1" value="${escapeHtml(editor.sandboxTmpfsSizeMb || "128")}" placeholder="128" ${busy}>
        </div>
        <div class="field">
          <label for="instance-form-sandbox-network">Sandbox Network Policy</label>
          <select id="instance-form-sandbox-network" ${busy}>
            <option value="default" ${editor.sandboxNetworkPolicy === "default" ? "selected" : ""}>default</option>
            <option value="none" ${editor.sandboxNetworkPolicy === "none" ? "selected" : ""}>none</option>
          </select>
          <p class="meta"><code>default</code> is recommended for Telegram, MCP, and cloud/provider APIs. Use <code>none</code> only for offline workloads.</p>
        </div>
        <div class="field">
          <label for="instance-form-sandbox-timeout">Sandbox Stop Timeout (seconds)</label>
          <input id="instance-form-sandbox-timeout" type="number" min="1" value="${escapeHtml(editor.sandboxTimeoutSeconds || "30")}" placeholder="30" ${busy}>
        </div>` : ""}
        </div>` : ""}
        ${editor.mode === "create" ? `
        <div class="field">
          <label for="instance-form-source-config">Source Config Override</label>
          <input id="instance-form-source-config" value="${escapeHtml(editor.sourceConfig)}" placeholder="/Users/rujirapongair/.nanobot/config.json" ${busy}>
          <p class="meta">Optional template config only. Leave empty to use default template behavior.</p>
        </div>` : ""}
      </div>
      ` : ""}
      <div class="inline-actions">
        <button id="instance-form-save" class="primary-button" ${busy || duplicate ? "disabled" : ""}>${editor.mode === "edit" ? "Save Instance" : "Add Instance"}</button>
      </div>
    </div>
    ${editor.mode === "edit" ? renderInstanceConfigEditor() : ""}
    ${renderInstanceDeleteConfirm()}
    </div>
  `;
  document.getElementById("instance-form-simple")?.addEventListener("click", () => {
    state.instanceEditor = { ...state.instanceEditor, advanced: false };
    renderInstanceForm();
    renderInstanceWorkspaceTabs();
  });
  document.getElementById("instance-form-advanced")?.addEventListener("click", () => {
    state.instanceEditor = { ...state.instanceEditor, advanced: true };
    renderInstanceForm();
    renderInstanceWorkspaceTabs();
  });
  document.getElementById("instance-form-save")?.addEventListener("click", handleInstanceFormSubmit);
  document.getElementById("instance-config-editor")?.addEventListener("input", handleInstanceConfigInput);
  document.getElementById("instance-config-save")?.addEventListener("click", handleInstanceConfigSave);
  document.getElementById("instance-config-reload")?.addEventListener("click", () => loadInstanceConfig(editor.targetId, true));
  document.getElementById("instance-delete-confirm")?.addEventListener("click", confirmInstanceDelete);
  document.getElementById("instance-delete-cancel")?.addEventListener("click", cancelInstanceDelete);
  document.getElementById("instance-form-runtime-mode")?.addEventListener("change", () => {
    syncInstanceEditorFromForm();
    renderInstanceForm();
  });
  document.getElementById("instance-form-sandbox-profile")?.addEventListener("change", () => {
    const profile = document.getElementById("instance-form-sandbox-profile")?.value || "balanced";
    state.instanceEditor = applySandboxProfileToEditor(syncInstanceEditorFromForm(), profile);
    renderInstanceForm();
  });
  document.getElementById("instance-runtime-override-toggle")?.addEventListener("click", () => {
    state.instanceEditor = {
      ...state.instanceEditor,
      runtimeOverrideOpen: !state.instanceEditor.runtimeOverrideOpen,
    };
    renderInstanceForm();
  });
  document.getElementById("instance-runtime-reset")?.addEventListener("click", () => {
    state.instanceEditor = applySandboxProfileToEditor(
      { ...syncInstanceEditorFromForm(), runtimeOverrideOpen: false },
      state.instanceEditor.sandboxProfile || "balanced",
    );
    renderInstanceForm();
  });
  ["instance-form-name", "instance-form-env", "instance-form-id", "instance-form-owner", "instance-form-repo-root", "instance-form-nanobot-bin", "instance-form-gateway-port", "instance-form-source-config", "instance-form-sandbox-image", "instance-form-sandbox-strategy", "instance-form-sandbox-cpu", "instance-form-sandbox-memory", "instance-form-sandbox-pids", "instance-form-sandbox-tmpfs", "instance-form-sandbox-network", "instance-form-sandbox-timeout"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", syncInstanceEditorFromForm);
    document.getElementById(id)?.addEventListener("change", syncInstanceEditorFromForm);
  });
}

function resetInstanceEditor() {
  state.instanceEditor = defaultInstanceEditor();
  state.instanceCreateOpen = false;
  state.selectedInstanceId = "";
  state.deleteCandidateId = "";
  state.instanceWorkspaceTab = "manage";
  state.skillsByInstance = {};
  state.runtimeAudit.instanceId = "";
  state.runtimeAudit.events = [];
  state.runtimeAudit.summary = null;
  state.runtimeAudit.nextCursor = null;
  state.runtimeAudit.initialized = false;
  state.runtimeAudit.loading = false;
  state.runtimeAudit.loadingMore = false;
  state.runtimeAudit.executionMode = "live";
  state.runtimeAudit.executionPinnedTraceId = "";
  state.runtimeAudit.executionLatestTraceId = "";
  state.runtimeAudit.executionUnreadTraceCount = 0;
  renderInstanceForm();
  renderInstances();
  syncLocationState();
}

function syncInstanceEditorFromForm() {
  state.instanceEditor = {
    ...state.instanceEditor,
    sandboxProfile: document.getElementById("instance-form-sandbox-profile")?.value || state.instanceEditor.sandboxProfile || "balanced",
    instanceId: document.getElementById("instance-form-id")?.value || state.instanceEditor.instanceId,
    name: document.getElementById("instance-form-name")?.value || "",
    owner: document.getElementById("instance-form-owner")?.value || "",
    env: document.getElementById("instance-form-env")?.value || "prod",
    repoRoot: document.getElementById("instance-form-repo-root")?.value || state.instanceEditor.repoRoot,
    nanobotBin: document.getElementById("instance-form-nanobot-bin")?.value || state.instanceEditor.nanobotBin,
    gatewayPort: document.getElementById("instance-form-gateway-port")?.value || "",
    sourceConfig: document.getElementById("instance-form-source-config")?.value || "",
    runtimeMode: document.getElementById("instance-form-runtime-mode")?.value || state.instanceEditor.runtimeMode || "sandbox",
    sandboxImage: document.getElementById("instance-form-sandbox-image")?.value || "",
    sandboxExecutionStrategy: (() => { const mode = document.getElementById("instance-form-runtime-mode")?.value || state.instanceEditor.runtimeMode || "sandbox"; const strategy = document.getElementById("instance-form-sandbox-strategy")?.value || "persistent"; return mode === "sandbox" && strategy === "tool_ephemeral" ? "persistent" : strategy; })(),
    sandboxCpuLimit: document.getElementById("instance-form-sandbox-cpu")?.value || "",
    sandboxMemoryLimit: document.getElementById("instance-form-sandbox-memory")?.value || "",
    sandboxPidsLimit: document.getElementById("instance-form-sandbox-pids")?.value || "256",
    sandboxTmpfsSizeMb: document.getElementById("instance-form-sandbox-tmpfs")?.value || "128",
    sandboxNetworkPolicy: document.getElementById("instance-form-sandbox-network")?.value || "default",
    sandboxTimeoutSeconds: document.getElementById("instance-form-sandbox-timeout")?.value || "30",
  };
  refreshInstancePreview();
  return state.instanceEditor;
}

function refreshInstancePreview() {
  const editor = state.instanceEditor || defaultInstanceEditor();
  const derived = deriveInstanceValues(editor);
  const softnixHome = inferSoftnixHome();
  const targetInstanceHome = `${softnixHome}/instances/${derived.instanceId.trim() || "<instance-id>"}`;
  const targetConfigPath = `${targetInstanceHome}/config.json`;
  const duplicate = editor.mode === "create"
    ? state.overview.instances.some((instance) => instance.id === derived.instanceId)
    : false;

  const setValue = (id, value) => {
    const element = document.getElementById(id);
    if (element) {
      if ("value" in element) {
        element.value = value;
      } else {
        element.textContent = value;
      }
    }
  };

  setValue("instance-preview-instance-id", derived.instanceId || "");
  setValue("instance-preview-owner", derived.owner || "");
  setValue("instance-preview-home", targetInstanceHome);
  setValue("instance-preview-config", targetConfigPath);
  setValue("instance-preview-port", (editor.gatewayPort || "").trim() || "Auto (next available)");
  setValue("instance-preview-runtime", editor.runtimeMode || "sandbox");
  setValue("instance-preview-template", derived.sourceConfig || "~/.nanobot/config.json (system default)");

  const note = document.getElementById("instance-preview-instance-id-note");
  if (note) {
    note.textContent = duplicate
      ? "Instance ID already exists in registry."
      : "Generated automatically from Name + Environment.";
    note.style.color = duplicate ? "#9a3e36" : "";
  }

  const saveButton = document.getElementById("instance-form-save");
  if (saveButton) {
    saveButton.disabled = duplicate || state.busyKey === "instance-form";
  }
}

function renderInstanceConfigEditor() {
  const cfg = state.instanceConfig;
  const loading = cfg.loading ? "disabled" : "";
  const saveDisabled = state.busyKey === "instance-config" || cfg.loading || !cfg.dirty ? "disabled" : "";
  return `
    <div class="instance-config-card">
      <div class="row-between">
        <div>
          <p class="eyebrow">Raw Config</p>
          <h4>config.json</h4>
        </div>
        <div class="inline-actions">
          <button id="instance-config-reload" class="secondary-button is-small" ${loading}>Reload</button>
          <button id="instance-config-save" class="primary-button is-small" ${saveDisabled}>Save config.json</button>
        </div>
      </div>
      <p class="meta">Direct JSON editor with schema validation on save.</p>
      <textarea id="instance-config-editor" class="code-textarea" ${loading}>${escapeHtml(cfg.text || "{}")}</textarea>
    </div>
  `;
}

async function selectInstanceForEdit(instanceId) {
  const instance = state.overview.instances.find((item) => item.id === instanceId);
  if (!instance) {
    return;
  }
  state.selectedInstanceId = instance.id;
  state.instanceCreateOpen = false;
  state.deleteCandidateId = "";
  state.instanceWorkspaceTab = "manage";
  ensureRuntimeAuditState(instance.id);
  state.instanceEditor = buildInstanceEditorFromInstance(instance);
  state.instanceEditor.runtimeOverrideOpen = isCustomRuntimeConfig(state.instanceEditor);
  renderInstanceForm();
  renderInstances();
  syncLocationState();
  await loadInstanceConfig(instance.id);
}

function renderInstanceDeleteConfirm() {
  if (!state.deleteCandidateId) {
    return "";
  }
  const instance = state.overview.instances.find((item) => item.id === state.deleteCandidateId);
  if (!instance) {
    return "";
  }
  return `
    <div class="instance-delete-card">
      <div class="row-between">
        <div>
          <p class="eyebrow">Danger Zone</p>
          <h4>Delete ${escapeHtml(instance.name)}</h4>
        </div>
        <span class="badge ${badgeClass("error")}">Confirm</span>
      </div>
      <p class="meta">This removes the instance from registry and permanently deletes its instance files, including the workspace directory on disk.</p>
      <div class="inline-actions">
        <button id="instance-delete-confirm" type="button" class="danger-button" ${state.busyKey === "instance-form" ? "disabled" : ""}>Delete Instance</button>
        <button id="instance-delete-cancel" type="button" class="secondary-button" ${state.busyKey === "instance-form" ? "disabled" : ""}>Cancel</button>
      </div>
    </div>
  `;
}

async function loadInstanceConfig(instanceId, showBannerOnError = false) {
  state.instanceConfig = {
    instanceId,
    text: state.instanceConfig.instanceId === instanceId ? state.instanceConfig.text : "{}",
    dirty: false,
    loading: true,
  };
  renderInstanceForm();
  try {
    const result = await fetchJson(`/admin/instances/${instanceId}/config`);
    state.instanceConfig = {
      instanceId,
      text: JSON.stringify(result.config, null, 2),
      dirty: false,
      loading: false,
    };
    renderInstanceForm();
  } catch (error) {
    state.instanceConfig = {
      instanceId,
      text: state.instanceConfig.text || "{}",
      dirty: false,
      loading: false,
    };
    renderInstanceForm();
    if (showBannerOnError) {
      setBanner(`Unable to load config.json: ${error.message}`, "error");
    }
  }
}

function handleInstanceConfigInput(event) {
  state.instanceConfig = {
    ...state.instanceConfig,
    text: event.target.value,
    dirty: true,
  };
  const saveButton = document.getElementById("instance-config-save");
  if (saveButton) {
    saveButton.disabled = false;
  }
}

async function handleInstanceConfigSave() {
  const instanceId = state.instanceConfig.instanceId || state.selectedInstanceId;
  if (!instanceId) {
    return;
  }
  let parsed;
  try {
    parsed = JSON.parse(state.instanceConfig.text || "{}");
  } catch (error) {
    setBanner(`Invalid JSON: ${error.message}`, "error");
    return;
  }

  state.busyKey = "instance-config";
  renderInstanceForm();
  try {
    const result = await patchJson(`/admin/instances/${instanceId}/config`, { config: parsed });
    await runAutoLifecycleAfterSave(result.instance || null);
    state.instanceConfig = {
      instanceId,
      text: JSON.stringify(result.config, null, 2),
      dirty: false,
      loading: false,
    };
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to save config.json: ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderInstanceForm();
  }
}

async function loadInstanceMemoryFiles(instanceId, { force = false } = {}) {
  if (!instanceId) return;
  const memoryState = getMemoryState(instanceId);
  const hasFiles = Object.keys(memoryState.files || {}).length > 0;
  if (memoryState.loading || (hasFiles && !force)) {
    return;
  }
  memoryState.loading = true;
  renderSelectedInstanceMemory(selectedInstance());
  try {
    const payload = await fetchJson(`/admin/instances/${encodeURIComponent(instanceId)}/memory-files`);
    const files = {};
    (payload.files || []).forEach((item) => {
      const filePath = String(item.path || "");
      if (!filePath) return;
      files[filePath] = {
        path: filePath,
        exists: !!item.exists,
        content: String(item.content || ""),
        originalContent: String(item.content || ""),
      };
    });
    memoryState.files = files;
    const paths = Object.keys(files);
    if (!paths.includes(memoryState.selectedPath)) {
      memoryState.selectedPath = paths[0] || "AGENTS.md";
    }
    clearBanner();
  } catch (error) {
    setBanner(`Unable to load memory files: ${error.message}`, "error");
  } finally {
    memoryState.loading = false;
    renderSelectedInstanceMemory(selectedInstance());
  }
}

function handleMemoryFileSelect(instanceId, path) {
  const memoryState = getMemoryState(instanceId);
  memoryState.selectedPath = path;
  renderSelectedInstanceMemory(selectedInstance());
}

function handleMemoryEditorInput(instanceId, path, value) {
  const memoryState = getMemoryState(instanceId);
  const file = memoryState.files[path];
  if (!file) return;
  file.content = value;
  renderSelectedInstanceMemory(selectedInstance());
}

async function handleMemoryFileSave(instanceId, path) {
  const memoryState = getMemoryState(instanceId);
  const file = memoryState.files[path];
  if (!file) return;
  const busyKey = `memory-save:${instanceId}:${path}`;
  state.busyKey = busyKey;
  renderSelectedInstanceMemory(selectedInstance());
  try {
    const payload = await patchJson(`/admin/instances/${encodeURIComponent(instanceId)}/memory-files`, {
      path,
      content: file.content,
    });
    file.content = String(payload.content || "");
    file.originalContent = String(payload.content || "");
    file.exists = !!payload.exists;
    clearBanner();
  } catch (error) {
    setBanner(`Unable to save ${path}: ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderSelectedInstanceMemory(selectedInstance());
  }
}

function handleMemoryFileReset(instanceId, path) {
  const memoryState = getMemoryState(instanceId);
  const file = memoryState.files[path];
  if (!file) return;
  file.content = file.originalContent || "";
  renderSelectedInstanceMemory(selectedInstance());
}

function renderSelectedInstanceSkills(instance) {
  const target = document.getElementById("instance-workspace-skills");
  if (!target || !instance) return;
  const skillState = getSkillState(instance.id);

  if (!skillState.skills.length && !skillState.loading) {
    void loadInstanceSkills(instance.id);
  }

  const canUpdate = hasAuthPermission("skills.update");
  const canDelete = hasAuthPermission("skills.delete");

  const skillCards = skillState.loading && !skillState.skills.length
    ? `<p class="meta">Loading skills…</p>`
    : skillState.skills.length === 0
      ? `<p class="meta">No skills found in workspace/skills/.</p>`
      : skillState.skills.map((skill) => {
        const isSelected = skillState.selectedSkill === skill.skill_name;
        const busyDel = state.busyKey === `skill-delete:${instance.id}:${skill.skill_name}` ? "disabled" : "";
        return `<div class="skill-card item-card ${isSelected ? "is-active" : ""}" data-skill-select="${escapeHtml(instance.id)}:${escapeHtml(skill.skill_name)}">
          <div class="row-between">
            <div style="min-width:0">
              <h4>${escapeHtml(skill.name || skill.skill_name)}</h4>
              ${skill.description ? `<p class="meta">${escapeHtml(skill.description)}</p>` : ""}
            </div>
            <span class="badge is-blue">${escapeHtml(String(skill.file_count))} file${skill.file_count !== 1 ? "s" : ""}</span>
          </div>
          ${canDelete ? `<div class="inline-actions" style="margin-top:6px">
            <button class="secondary-button is-small is-danger" data-skill-delete="${escapeHtml(instance.id)}:${escapeHtml(skill.skill_name)}" ${busyDel}>Delete</button>
          </div>` : ""}
        </div>`;
      }).join("");

  const selectedSkillName = skillState.selectedSkill;
  const files = Object.values(skillState.files || {});
  const selectedFilePath = skillState.selectedFile;
  const selectedFileObj = selectedFilePath ? skillState.files[selectedFilePath] : null;

  const fileNav = selectedSkillName
    ? files.length
      ? files.map((f) => {
        const dirty = f.content !== f.originalContent;
        return `<button class="console-tab ${f.path === selectedFilePath ? "is-active" : ""}" data-skill-file="${escapeHtml(instance.id)}:${escapeHtml(selectedSkillName)}:${escapeHtml(f.path)}">${escapeHtml(f.path)}${dirty ? " *" : ""}</button>`;
      }).join("")
      : `<p class="meta">${skillState.loadingFiles ? "Loading files…" : "No files found."}</p>`
    : `<p class="meta">Select a skill to view files.</p>`;

  const editorBody = selectedFileObj
    ? (() => {
      const dirty = selectedFileObj.content !== selectedFileObj.originalContent;
      const busyKey = `skill-save:${instance.id}:${selectedSkillName}:${selectedFileObj.path}`;
      const busy = state.busyKey === busyKey ? "disabled" : "";
      return `<div class="item-card">
        <div class="row-between">
          <h4>Edit · ${escapeHtml(selectedFileObj.path)}</h4>
          <span class="badge ${badgeClass(dirty ? "warning" : "ok")}">${dirty ? "Unsaved" : "Saved"}</span>
        </div>
        <div class="field">
          <label>Content</label>
          <textarea class="memory-editor-textarea" data-skill-editor="${escapeHtml(instance.id)}:${escapeHtml(selectedSkillName)}:${escapeHtml(selectedFileObj.path)}" ${busy}>${escapeHtml(selectedFileObj.content)}</textarea>
        </div>
        ${canUpdate ? `<div class="inline-actions">
          <button class="primary-button is-small" data-skill-save="${escapeHtml(instance.id)}:${escapeHtml(selectedSkillName)}:${escapeHtml(selectedFileObj.path)}" ${busy || !dirty ? "disabled" : ""}>Save</button>
          <button class="secondary-button is-small" data-skill-reset="${escapeHtml(instance.id)}:${escapeHtml(selectedSkillName)}:${escapeHtml(selectedFileObj.path)}" ${busy || !dirty ? "disabled" : ""}>Reset</button>
        </div>` : ""}
      </div>`;
    })()
    : selectedSkillName
      ? `<div class="item-card"><h4>No file selected</h4><p class="meta">${skillState.loadingFiles ? "Loading…" : "Choose a file from the list."}</p></div>`
      : `<div class="item-card"><h4>No skill selected</h4><p class="meta">Click a skill from the list to view its files.</p></div>`;

  target.innerHTML = `
    <div class="memory-workspace">
      <div class="memory-nav item-card">
        <div class="row-between">
          <h4>Skills</h4>
          <button class="secondary-button is-small" data-skills-reload="${escapeHtml(instance.id)}" ${skillState.loading ? "disabled" : ""}>Refresh</button>
        </div>
        <p class="meta">Skills loaded from workspace/skills/.</p>
        <div class="skill-card-list">${skillCards}</div>
      </div>
      <div class="stack">
        ${selectedSkillName ? `<div class="memory-file-list" style="margin-bottom:8px">${fileNav}</div>` : ""}
        ${editorBody}
      </div>
    </div>
  `;

  target.querySelectorAll("[data-skill-select]").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.target.closest("[data-skill-delete]")) return;
      const [instanceId, skillName] = el.dataset.skillSelect.split(":");
      handleSkillSelect(instanceId, skillName);
    });
  });
  target.querySelectorAll("[data-skill-file]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const parts = btn.dataset.skillFile.split(":");
      handleSkillFileSelect(parts[0], parts[1], parts.slice(2).join(":"));
    });
  });
  target.querySelectorAll("[data-skill-editor]").forEach((ta) => {
    ta.addEventListener("input", () => {
      const parts = ta.dataset.skillEditor.split(":");
      handleSkillEditorInput(parts[0], parts[1], parts.slice(2).join(":"), ta.value);
    });
  });
  target.querySelectorAll("[data-skill-save]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const parts = btn.dataset.skillSave.split(":");
      void handleSkillFileSave(parts[0], parts[1], parts.slice(2).join(":"));
    });
  });
  target.querySelectorAll("[data-skill-reset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const parts = btn.dataset.skillReset.split(":");
      handleSkillFileReset(parts[0], parts[1], parts.slice(2).join(":"));
    });
  });
  target.querySelectorAll("[data-skill-delete]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const [instanceId, skillName] = btn.dataset.skillDelete.split(":");
      void handleSkillDelete(instanceId, skillName);
    });
  });
  target.querySelectorAll("[data-skills-reload]").forEach((btn) => {
    btn.addEventListener("click", () => void loadInstanceSkills(btn.dataset.skillsReload, { force: true }));
  });
}

async function loadInstanceSkills(instanceId, { force = false } = {}) {
  if (!instanceId) return;
  const skillState = getSkillState(instanceId);
  if (skillState.loading || (skillState.skills.length && !force)) return;
  skillState.loading = true;
  renderSelectedInstanceSkills(selectedInstance());
  try {
    const payload = await fetchJson(`/admin/instances/${encodeURIComponent(instanceId)}/skills`);
    skillState.skills = payload.skills || [];
    if (skillState.selectedSkill && !skillState.skills.find((s) => s.skill_name === skillState.selectedSkill)) {
      skillState.selectedSkill = null;
      skillState.files = {};
      skillState.selectedFile = null;
    }
    clearBanner();
  } catch (error) {
    setBanner(`Unable to load skills: ${error.message}`, "error");
  } finally {
    skillState.loading = false;
    renderSelectedInstanceSkills(selectedInstance());
  }
}

async function loadInstanceSkillFiles(instanceId, skillName) {
  if (!instanceId || !skillName) return;
  const skillState = getSkillState(instanceId);
  skillState.loadingFiles = true;
  renderSelectedInstanceSkills(selectedInstance());
  try {
    const payload = await fetchJson(`/admin/instances/${encodeURIComponent(instanceId)}/skills/${encodeURIComponent(skillName)}`);
    const files = {};
    (payload.files || []).forEach((item) => {
      const p = String(item.path || "");
      if (!p) return;
      files[p] = { path: p, content: String(item.content || ""), originalContent: String(item.content || "") };
    });
    skillState.files = files;
    const paths = Object.keys(files);
    if (!paths.includes(skillState.selectedFile)) {
      skillState.selectedFile = paths.find((p) => p === "SKILL.md") || paths[0] || null;
    }
    clearBanner();
  } catch (error) {
    setBanner(`Unable to load skill files: ${error.message}`, "error");
  } finally {
    skillState.loadingFiles = false;
    renderSelectedInstanceSkills(selectedInstance());
  }
}

function handleSkillSelect(instanceId, skillName) {
  const skillState = getSkillState(instanceId);
  if (skillState.selectedSkill === skillName) return;
  skillState.selectedSkill = skillName;
  skillState.files = {};
  skillState.selectedFile = null;
  renderSelectedInstanceSkills(selectedInstance());
  void loadInstanceSkillFiles(instanceId, skillName);
}

function handleSkillFileSelect(instanceId, skillName, path) {
  const skillState = getSkillState(instanceId);
  skillState.selectedFile = path;
  renderSelectedInstanceSkills(selectedInstance());
}

function handleSkillEditorInput(instanceId, skillName, path, value) {
  const skillState = getSkillState(instanceId);
  const file = skillState.files[path];
  if (!file) return;
  file.content = value;
  renderSelectedInstanceSkills(selectedInstance());
}

async function handleSkillFileSave(instanceId, skillName, path) {
  const skillState = getSkillState(instanceId);
  const file = skillState.files[path];
  if (!file) return;
  const busyKey = `skill-save:${instanceId}:${skillName}:${path}`;
  state.busyKey = busyKey;
  renderSelectedInstanceSkills(selectedInstance());
  try {
    const result = await patchJson(
      `/admin/instances/${encodeURIComponent(instanceId)}/skills/${encodeURIComponent(skillName)}`,
      { path, content: file.content },
    );
    file.originalContent = result.content ?? file.content;
    clearBanner();
  } catch (error) {
    setBanner(`Unable to save skill file: ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderSelectedInstanceSkills(selectedInstance());
  }
}

function handleSkillFileReset(instanceId, skillName, path) {
  const skillState = getSkillState(instanceId);
  const file = skillState.files[path];
  if (!file) return;
  file.content = file.originalContent || "";
  renderSelectedInstanceSkills(selectedInstance());
}

async function handleSkillDelete(instanceId, skillName) {
  if (!confirm(`Delete skill "${skillName}" and all its files? This cannot be undone.`)) return;
  const busyKey = `skill-delete:${instanceId}:${skillName}`;
  state.busyKey = busyKey;
  renderSelectedInstanceSkills(selectedInstance());
  try {
    await deleteJson(`/admin/instances/${encodeURIComponent(instanceId)}/skills/${encodeURIComponent(skillName)}`);
    const skillState = getSkillState(instanceId);
    if (skillState.selectedSkill === skillName) {
      skillState.selectedSkill = null;
      skillState.files = {};
      skillState.selectedFile = null;
    }
    await loadInstanceSkills(instanceId, { force: true });
    clearBanner();
  } catch (error) {
    setBanner(`Unable to delete skill: ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderSelectedInstanceSkills(selectedInstance());
  }
}

function getFilteredLiveEvents() {
  const events = state.activity?.events || [];
  if (state.liveInstanceFilter === "all") {
    return events;
  }
  return events.filter((event) => event.instance_id === state.liveInstanceFilter);
}

function previewText(value, max = 180) {
  const text = String(value || "");
  if (text.length <= max) {
    return text;
  }
  return `${text.slice(0, max)}...`;
}

function liveEventKey(event, index) {
  return `${event.instance_id || "unknown"}|${event.session_key || "session"}|${event.ts || "ts"}|${index}`;
}

function renderLiveFilterOptions() {
  const select = document.getElementById("live-instance-filter");
  if (!select) return;
  const instances = state.overview?.instances || [];
  const options = [
    '<option value="all">All instances</option>',
    ...instances.map(
      (instance) =>
        `<option value="${escapeHtml(instance.id)}" ${
          state.liveInstanceFilter === instance.id ? "selected" : ""
        }>${escapeHtml(instance.name)}</option>`,
    ),
  ];
  select.innerHTML = options.join("");
  if (!instances.some((instance) => instance.id === state.liveInstanceFilter)) {
    state.liveInstanceFilter = "all";
    select.value = "all";
  }
  syncLocationState();
}

function renderLiveActivity() {
  const events = getFilteredLiveEvents();
  const target = document.getElementById("live-activity-list");
  if (!events.length) {
    target.innerHTML = `<div class="item-card"><h4>No activity yet</h4><p>Recent session and cron events will appear here.</p></div>`;
    return;
  }

  target.innerHTML = events
    .map(
      (event, index) => {
        const key = liveEventKey(event, index);
        const expanded = !!state.liveExpandedEvents[key];
        const fullText = event.detail || event.summary || "";
        const shownText = expanded ? fullText : previewText(fullText);
        return `
        <article class="event-row event-row-clickable ${expanded ? "is-expanded" : ""}" data-live-event-key="${escapeHtml(key)}">
          <div class="row-between">
            <div class="event-meta">
              <span class="badge ${badgeClass(event.severity)}">${escapeHtml(event.type)}</span>
              <span>${escapeHtml(event.instance_name)}</span>
              <span>${escapeHtml(event.channel)}</span>
              <span>${escapeHtml(event.actor)}</span>
            </div>
            <span class="meta">${escapeHtml(event.ts || "")}</span>
          </div>
          <div class="event-summary">${escapeHtml(shownText)}</div>
          <div class="meta">${expanded ? "Click to collapse" : "Click to view full message"}</div>
          <div class="event-meta">
            <span>Session: ${escapeHtml(event.session_key)}</span>
          </div>
        </article>
      `;
      },
    )
    .join("");

  target.querySelectorAll("[data-live-event-key]").forEach((card) => {
    card.addEventListener("click", () => {
      const key = card.dataset.liveEventKey;
      if (!key) return;
      state.liveExpandedEvents[key] = !state.liveExpandedEvents[key];
      renderLiveActivity();
    });
  });
}

function renderLiveSummary() {
  const events = getFilteredLiveEvents();
  const target = document.getElementById("live-summary-list");
  const counts = {
    inbound: 0,
    outbound: 0,
    tool: 0,
    cron: 0,
  };

  events.forEach((event) => {
    if (event.type in counts) {
      counts[event.type] += 1;
    }
  });

  target.innerHTML = [
    { label: "Inbound", value: counts.inbound, note: "Recent user messages" },
    { label: "Outbound", value: counts.outbound, note: "Recent assistant responses" },
    { label: "Tool", value: counts.tool, note: "Tool execution traces" },
    { label: "Cron", value: counts.cron, note: "Recent schedule executions" },
  ]
    .map(
      (item) => `
        <div class="item-card">
          <p class="metric-label">${escapeHtml(item.label)}</p>
          <h4 class="metric-value" style="font-size: 26px">${escapeHtml(item.value)}</h4>
          <p class="meta">${escapeHtml(item.note)}</p>
        </div>
      `,
    )
    .join("");
}

function renderProviders() {
  const target = document.getElementById("providers-editor");
  target.innerHTML = state.overview.instances
    .map((instance) => {
      const defaultKey = `provider-default:${instance.id}`;
      const defaultBusy = state.busyKey === defaultKey ? "disabled" : "";
      const providerOptions = ['<option value="auto">auto</option>']
        .concat(
          (instance.providers || []).map(
            (provider) => `<option value="${escapeHtml(provider.name)}" ${provider.name === instance.selected_provider ? "selected" : ""}>${escapeHtml(provider.label || provider.name)}</option>`,
          ),
        )
        .join("");

      const providerCards = (instance.providers || [])
        .map((provider) => {
          const key = `provider:${instance.id}:${provider.name}`;
          const disabled = state.busyKey === key ? "disabled" : "";
          return `
            <div class="item-card">
              <div class="row-between">
                <div>
                  <h4>${escapeHtml(provider.label)}</h4>
                  <p class="meta">${provider.oauth ? "OAuth provider" : "API-based provider"}</p>
                </div>
                <span class="badge ${badgeClass(provider.configured ? "ok" : "neutral")}">${provider.configured ? "Configured" : "Not set"}</span>
              </div>
              <div class="field">
                <label for="provider-base-${escapeHtml(key)}">API Base</label>
                <input id="provider-base-${escapeHtml(key)}" data-provider-base="${escapeHtml(key)}" value="${escapeHtml(provider.api_base || "")}" ${disabled}>
              </div>
              <div class="field">
                <label for="provider-key-${escapeHtml(key)}">API Key</label>
                <input id="provider-key-${escapeHtml(key)}" data-provider-key="${escapeHtml(key)}" value="" placeholder="${escapeHtml(provider.api_key_masked || "")}" ${disabled}>
              </div>
              <div class="field">
                <label for="provider-headers-${escapeHtml(key)}">Extra Headers (JSON object)</label>
                <textarea id="provider-headers-${escapeHtml(key)}" data-provider-headers="${escapeHtml(key)}" ${disabled}>${escapeHtml(JSON.stringify(provider.extra_headers || {}, null, 2))}</textarea>
              </div>
              <div class="inline-actions">
                <button class="primary-button" data-provider-save="${escapeHtml(key)}" ${disabled}>Save</button>
                <button class="secondary-button" data-provider-validate="${escapeHtml(key)}" ${disabled}>Validate</button>
              </div>
            </div>
          `;
        })
        .join("");

      return `
        <section class="panel">
          <div class="panel-header">
            <div>
              <p class="eyebrow">Instance</p>
              <h3>${escapeHtml(instance.name)}</h3>
            </div>
          </div>
          <div class="item-card">
            <div class="row-between">
              <div>
                <h4>Default Routing</h4>
                <p class="meta">Select the model and provider used for new requests.</p>
              </div>
            </div>
            <div class="field">
              <label for="default-model-${escapeHtml(instance.id)}">Model</label>
              <input id="default-model-${escapeHtml(instance.id)}" data-default-model="${escapeHtml(instance.id)}" value="${escapeHtml(instance.model || "")}" ${defaultBusy}>
            </div>
            <div class="field">
              <label for="default-provider-${escapeHtml(instance.id)}">Provider</label>
              <select id="default-provider-${escapeHtml(instance.id)}" data-default-provider="${escapeHtml(instance.id)}" ${defaultBusy}>${providerOptions}</select>
            </div>
            <div class="inline-actions">
              <button class="primary-button" data-provider-default-save="${escapeHtml(instance.id)}" ${defaultBusy}>Save Defaults</button>
            </div>
          </div>
          <div class="stack">${providerCards}</div>
        </section>
      `;
    })
    .join("");

  target.querySelectorAll("[data-provider-default-save]").forEach((button) => {
    button.addEventListener("click", () => handleProviderDefaultsSave(button.dataset.providerDefaultSave));
  });
  target.querySelectorAll("[data-provider-save]").forEach((button) => {
    button.addEventListener("click", () => handleProviderSave(button.dataset.providerSave));
  });
  target.querySelectorAll("[data-provider-validate]").forEach((button) => {
    button.addEventListener("click", () => handleProviderValidate(button.dataset.providerValidate));
  });
}

function renderSchedules() {
  const target = document.getElementById("schedules-list");
  const instances = state.schedules?.instances || [];
  target.innerHTML = instances
    .map((instance) => {
      const jobs = (instance.jobs || [])
        .map((job) => {
          const toggleKey = `schedule-toggle:${instance.instance_id}:${job.id}`;
          const runKey = `schedule-run:${instance.instance_id}:${job.id}`;
          const deleteKey = `schedule-delete:${instance.instance_id}:${job.id}`;
          const disabledToggle = state.busyKey === toggleKey ? "disabled" : "";
          const disabledRun = state.busyKey === runKey ? "disabled" : "";
          const disabledDelete = state.busyKey === deleteKey ? "disabled" : "";
          const scheduleLabel =
            job.schedule.kind === "every"
              ? `Every ${job.schedule.every_ms || 0} ms`
              : job.schedule.kind === "cron"
                ? `Cron: ${job.schedule.expr || ""}${job.schedule.tz ? ` (${job.schedule.tz})` : ""}`
                : `At ${job.schedule.at_ms || ""}`;
          return `
            <div class="item-card">
              <div class="row-between">
                <div>
                  <h4>${escapeHtml(job.name)}</h4>
                  <p class="meta">${escapeHtml(scheduleLabel)}</p>
                </div>
                <span class="badge ${badgeClass(job.enabled ? "ok" : "neutral")}">${job.enabled ? "Enabled" : "Disabled"}</span>
              </div>
              <p class="meta">Last status: ${escapeHtml(job.state.last_status || "never")} · Next: ${escapeHtml(job.state.next_run_at_ms || "n/a")}</p>
              <p class="event-summary">${escapeHtml(job.payload.message || "")}</p>
              <div class="inline-actions">
                <button class="secondary-button" data-schedule-toggle="${escapeHtml(toggleKey)}" ${disabledToggle}>${job.enabled ? "Disable" : "Enable"}</button>
                <button class="primary-button" data-schedule-run="${escapeHtml(runKey)}" ${disabledRun}>Run Now</button>
                <button class="secondary-button" data-schedule-delete="${escapeHtml(deleteKey)}" ${disabledDelete}>Delete</button>
              </div>
            </div>
          `;
        })
        .join("");
      return `
        <section class="panel">
          <div class="panel-header">
            <div>
              <p class="eyebrow">Instance</p>
              <h3>${escapeHtml(instance.instance_name)}</h3>
            </div>
          </div>
          <div class="stack">${jobs || `<div class="item-card"><h4>No schedules</h4><p class="meta">Create one from the form on the right.</p></div>`}</div>
        </section>
      `;
    })
    .join("");

  target.querySelectorAll("[data-schedule-toggle]").forEach((button) => {
    button.addEventListener("click", () => handleScheduleToggle(button.dataset.scheduleToggle));
  });
  target.querySelectorAll("[data-schedule-run]").forEach((button) => {
    button.addEventListener("click", () => handleScheduleRun(button.dataset.scheduleRun));
  });
  target.querySelectorAll("[data-schedule-delete]").forEach((button) => {
    button.addEventListener("click", () => handleScheduleDelete(button.dataset.scheduleDelete));
  });
}

function renderScheduleCreateForm() {
  const target = document.getElementById("schedule-create-form");
  const instanceOptions = (state.overview?.instances || [])
    .map((instance) => `<option value="${escapeHtml(instance.id)}">${escapeHtml(instance.name)}</option>`)
    .join("");
  const disabled = state.busyKey === "schedule-create" ? "disabled" : "";
  target.innerHTML = `
    <div class="field">
      <label for="schedule-instance">Instance</label>
      <select id="schedule-instance" ${disabled}>${instanceOptions}</select>
    </div>
    <div class="field">
      <label for="schedule-name">Name</label>
      <input id="schedule-name" ${disabled}>
    </div>
    <div class="field">
      <label for="schedule-kind">Schedule Type</label>
      <select id="schedule-kind" ${disabled}>
        <option value="every">Every</option>
        <option value="cron">Cron</option>
        <option value="at">At</option>
      </select>
    </div>
    <div class="field">
      <label for="schedule-every-ms">Every (ms)</label>
      <input id="schedule-every-ms" type="number" value="60000" ${disabled}>
    </div>
    <div class="field">
      <label for="schedule-cron-expr">Cron Expression</label>
      <input id="schedule-cron-expr" placeholder="0 9 * * *" ${disabled}>
    </div>
    <div class="field">
      <label for="schedule-cron-tz">Timezone</label>
      <input id="schedule-cron-tz" placeholder="Asia/Bangkok" ${disabled}>
    </div>
    <div class="field">
      <label for="schedule-at-ms">At (unix ms)</label>
      <input id="schedule-at-ms" type="number" ${disabled}>
    </div>
    <div class="field">
      <label for="schedule-message">Message</label>
      <textarea id="schedule-message" ${disabled}></textarea>
    </div>
    <div class="field">
      <label>
        <input id="schedule-deliver" type="checkbox" ${disabled}>
        Deliver response to a channel target
      </label>
    </div>
    <div class="field">
      <label for="schedule-channel">Channel</label>
      <input id="schedule-channel" placeholder="telegram" ${disabled}>
    </div>
    <div class="field">
      <label for="schedule-to">Recipient</label>
      <input id="schedule-to" placeholder="chat id / recipient id" ${disabled}>
    </div>
    <div class="field">
      <label>
        <input id="schedule-delete-after-run" type="checkbox" ${disabled}>
        Delete after run
      </label>
    </div>
    <div class="inline-actions">
      <button id="schedule-create-button" class="primary-button" ${disabled}>Create Schedule</button>
    </div>
  `;
  document.getElementById("schedule-create-button")?.addEventListener("click", handleScheduleCreate);
}

function renderMcp() {
  const target = document.getElementById("mcp-editor");
  target.innerHTML = state.overview.instances
    .map((instance) => {
      const cards = (instance.mcp.servers || [])
        .map((server) => {
          const key = `mcp:${instance.id}:${server.name}`;
          const disabled = state.busyKey === key ? "disabled" : "";
          return `
            <div class="item-card">
              <div class="row-between">
                <div>
                  <h4>${escapeHtml(server.name)}</h4>
                  <p class="meta">${escapeHtml(server.type || "unknown")} · timeout ${escapeHtml(server.tool_timeout)}s</p>
                </div>
                <span class="badge ${badgeClass("ok")}">Configured</span>
              </div>
              <div class="field">
                <label for="mcp-type-${escapeHtml(key)}">Type</label>
                <select id="mcp-type-${escapeHtml(key)}" data-mcp-type="${escapeHtml(key)}" ${disabled}>
                  <option value="">auto</option>
                  <option value="stdio" ${server.type === "stdio" ? "selected" : ""}>stdio</option>
                  <option value="sse" ${server.type === "sse" ? "selected" : ""}>sse</option>
                  <option value="streamableHttp" ${server.type === "streamableHttp" ? "selected" : ""}>streamableHttp</option>
                </select>
              </div>
              <div class="field">
                <label for="mcp-command-${escapeHtml(key)}">Command</label>
                <input id="mcp-command-${escapeHtml(key)}" data-mcp-command="${escapeHtml(key)}" value="${escapeHtml(server.command || "")}" ${disabled}>
              </div>
              <div class="field">
                <label for="mcp-url-${escapeHtml(key)}">URL</label>
                <input id="mcp-url-${escapeHtml(key)}" data-mcp-url="${escapeHtml(key)}" value="${escapeHtml(server.url || "")}" ${disabled}>
              </div>
              <div class="field">
                <label for="mcp-timeout-${escapeHtml(key)}">Tool Timeout</label>
                <input id="mcp-timeout-${escapeHtml(key)}" data-mcp-timeout="${escapeHtml(key)}" type="number" value="${escapeHtml(server.tool_timeout || 30)}" ${disabled}>
              </div>
              <div class="field">
                <label for="mcp-args-${escapeHtml(key)}">Args (JSON array)</label>
                <textarea id="mcp-args-${escapeHtml(key)}" data-mcp-args="${escapeHtml(key)}" ${disabled}>${escapeHtml(JSON.stringify(server.args || [], null, 2))}</textarea>
              </div>
              <div class="field">
                <label for="mcp-headers-${escapeHtml(key)}">Headers (JSON object)</label>
                <textarea id="mcp-headers-${escapeHtml(key)}" data-mcp-headers="${escapeHtml(key)}" ${disabled}>${escapeHtml(JSON.stringify(server.headers || {}, null, 2))}</textarea>
              </div>
              <div class="inline-actions">
                <button class="primary-button" data-mcp-save="${escapeHtml(key)}" ${disabled}>Save</button>
                <button class="secondary-button" data-mcp-validate="${escapeHtml(key)}" ${disabled}>Validate</button>
                <button class="secondary-button" data-mcp-delete="${escapeHtml(key)}" ${disabled}>Delete</button>
              </div>
            </div>
          `;
        })
        .join("");

      const createKey = `mcp-create:${instance.id}`;
      const createDisabled = state.busyKey === createKey ? "disabled" : "";
      return `
        <section class="panel">
          <div class="panel-header">
            <div>
              <p class="eyebrow">Instance</p>
              <h3>${escapeHtml(instance.name)}</h3>
            </div>
          </div>
          <div class="item-card">
            <div class="row-between">
              <div>
                <h4>Add MCP Server</h4>
                <p class="meta">Create a new MCP server definition.</p>
              </div>
            </div>
            <div class="field">
              <label for="mcp-create-name-${escapeHtml(instance.id)}">Server Name</label>
              <input id="mcp-create-name-${escapeHtml(instance.id)}" data-mcp-create-name="${escapeHtml(instance.id)}" ${createDisabled}>
            </div>
            <div class="field">
              <label for="mcp-create-type-${escapeHtml(instance.id)}">Type</label>
              <select id="mcp-create-type-${escapeHtml(instance.id)}" data-mcp-create-type="${escapeHtml(instance.id)}" ${createDisabled}>
                <option value="">auto</option>
                <option value="stdio">stdio</option>
                <option value="sse">sse</option>
                <option value="streamableHttp">streamableHttp</option>
              </select>
            </div>
            <div class="field">
              <label for="mcp-create-command-${escapeHtml(instance.id)}">Command</label>
              <input id="mcp-create-command-${escapeHtml(instance.id)}" data-mcp-create-command="${escapeHtml(instance.id)}" ${createDisabled}>
            </div>
            <div class="field">
              <label for="mcp-create-url-${escapeHtml(instance.id)}">URL</label>
              <input id="mcp-create-url-${escapeHtml(instance.id)}" data-mcp-create-url="${escapeHtml(instance.id)}" ${createDisabled}>
            </div>
            <div class="field">
              <label for="mcp-create-timeout-${escapeHtml(instance.id)}">Tool Timeout</label>
              <input id="mcp-create-timeout-${escapeHtml(instance.id)}" data-mcp-create-timeout="${escapeHtml(instance.id)}" type="number" value="30" ${createDisabled}>
            </div>
            <div class="field">
              <label for="mcp-create-args-${escapeHtml(instance.id)}">Args (JSON array)</label>
              <textarea id="mcp-create-args-${escapeHtml(instance.id)}" data-mcp-create-args="${escapeHtml(instance.id)}" ${createDisabled}>[]</textarea>
            </div>
            <div class="field">
              <label for="mcp-create-headers-${escapeHtml(instance.id)}">Headers (JSON object)</label>
              <textarea id="mcp-create-headers-${escapeHtml(instance.id)}" data-mcp-create-headers="${escapeHtml(instance.id)}" ${createDisabled}>{}</textarea>
            </div>
            <div class="inline-actions">
              <button class="primary-button" data-mcp-create-save="${escapeHtml(instance.id)}" ${createDisabled}>Add Server</button>
            </div>
          </div>
          <div class="stack">${cards || `<div class="item-card"><h4>No MCP servers</h4><p class="meta">Add one when this instance needs external tool servers.</p></div>`}</div>
        </section>
      `;
    })
    .join("");

  target.querySelectorAll("[data-mcp-save]").forEach((button) => {
    button.addEventListener("click", () => handleMcpSave(button.dataset.mcpSave));
  });
  target.querySelectorAll("[data-mcp-validate]").forEach((button) => {
    button.addEventListener("click", () => handleMcpValidate(button.dataset.mcpValidate));
  });
  target.querySelectorAll("[data-mcp-delete]").forEach((button) => {
    button.addEventListener("click", () => handleMcpDelete(button.dataset.mcpDelete));
  });
  target.querySelectorAll("[data-mcp-create-save]").forEach((button) => {
    button.addEventListener("click", () => handleMcpCreate(button.dataset.mcpCreateSave));
  });
}

function renderChannels() {
  const target = document.getElementById("channels-editor");
  target.innerHTML = state.overview.instances
    .map((instance) => {
      const channels = instance.channels
        .map((channel) => {
          const key = `${instance.id}:${channel.name}`;
          const allowList = channel.allow_from_mode === "deny_all" ? "" : "";
          const originalChannel = state.channels.find(
            (item) => item.instance_id === instance.id && item.name === channel.name,
          );
          const disabled = state.busyKey === key ? "disabled" : "";
          const isSoftnix = channel.name === "softnix_app";
          const displayName = isSoftnix ? "Softnix Mobile" : channel.name;
          const placeholder = isSoftnix ? "One Device ID per line" : "One user identifier per line";
          const currentAllow =
            originalChannel && Array.isArray(originalChannel.allow_from)
              ? originalChannel.allow_from.join("\n")
              : "";
          return `
            <div class="item-card">
              <div class="row-between">
                <div>
                  <h4>${escapeHtml(displayName)}</h4>
                  <p class="meta">Access mode: ${escapeHtml(channel.allow_from_mode)}</p>
                </div>
                <span class="badge ${badgeClass(channel.enabled ? "ok" : "neutral")}">${channel.enabled ? "Enabled" : "Disabled"}</span>
              </div>
              <div class="field">
                <label>
                  <input type="checkbox" data-channel-enabled="${escapeHtml(key)}" ${channel.enabled ? "checked" : ""} ${disabled}>
                  Enabled
                </label>
              </div>
              <div class="field">
                <label for="allow-${escapeHtml(key)}">Allowlist</label>
                <textarea id="allow-${escapeHtml(key)}" data-channel-allow="${escapeHtml(key)}" ${disabled} placeholder="${escapeHtml(placeholder)}">${escapeHtml(currentAllow || allowList)}</textarea>
              </div>
              <div class="inline-actions">
                <button class="primary-button" data-channel-save="${escapeHtml(key)}" ${disabled}>Save</button>
                <span class="hint">Use <code>*</code> on its own line to allow everyone.</span>
              </div>
            </div>
          `;
        })
        .join("");

      return `
        <section class="panel">
          <div class="panel-header">
            <div>
              <p class="eyebrow">Instance</p>
              <h3>${escapeHtml(instance.name)}</h3>
            </div>
          </div>
          <div class="stack">${channels}</div>
        </section>
      `;
    })
    .join("");

  target.querySelectorAll("[data-channel-save]").forEach((button) => {
    button.addEventListener("click", () => handleChannelSave(button.dataset.channelSave));
  });
}

function renderSecurityTable() {
  const target = document.getElementById("security-table");
  if (!target) return;
  target.innerHTML = state.security.findings
    .map(
      (finding) => `
        <div class="item-card">
          <div class="row-between">
            <div>
              <h4>${escapeHtml(finding.title)}</h4>
              <p class="meta">${escapeHtml(finding.instance_name)} · ${escapeHtml(finding.code)}</p>
            </div>
            <span class="badge ${badgeClass(finding.severity)}">${escapeHtml(finding.severity)}</span>
          </div>
          <p>${escapeHtml(finding.detail)}</p>
        </div>
      `,
    )
    .join("");
}

function renderSecurityControls() {
  const target = document.getElementById("security-controls");
  target.innerHTML = state.overview.instances
    .map((instance) => {
      const key = `restriction:${instance.id}`;
      const disabled = state.busyKey === key ? "disabled" : "";
      const checked = instance.security.findings.every((finding) => finding.code !== "workspace_restriction_disabled");
      const sandboxProfile = normalizeSandboxProfile(instance.runtime_config?.sandbox?.profile || "balanced");
      const impact = runtimeImpactSummary({
        runtimeMode: instance.runtime_config?.mode || "host",
        sandboxExecutionStrategy: instance.runtime_config?.sandbox?.executionStrategy || "persistent",
        sandboxNetworkPolicy: instance.runtime_config?.sandbox?.networkPolicy || "default",
      });
      return `
        <div class="item-card">
          <div class="row-between">
            <div>
              <h4>${escapeHtml(instance.name)}</h4>
              <p class="meta">Restrict agent tools to the configured workspace directory.</p>
            </div>
            <span class="badge ${badgeClass(checked ? "ok" : "warning")}">${checked ? "Restricted" : "Open"}</span>
          </div>
          <div class="field">
            <label>Runtime Profile</label>
            <p><strong>${escapeHtml(sandboxProfileLabel(sandboxProfile))}</strong></p>
            <p class="meta">${escapeHtml(sandboxProfileSummary(sandboxProfile))}</p>
          </div>
          <div class="field">
            <label>Internet Access</label>
            <p><strong>${escapeHtml(impact.internet)}</strong></p>
          </div>
          <div class="field">
            <label>Tool Execution</label>
            <p><strong>${escapeHtml(impact.toolExecution)}</strong></p>
          </div>
          <div class="field">
            <label>
              <input type="checkbox" data-restriction-toggle="${escapeHtml(instance.id)}" ${checked ? "checked" : ""} ${disabled}>
              Restrict tools to workspace
            </label>
          </div>
          <div class="inline-actions">
            <button class="primary-button" data-restriction-save="${escapeHtml(instance.id)}" ${disabled}>Save</button>
          </div>
        </div>
      `;
    })
    .join("");
  target.querySelectorAll("[data-restriction-save]").forEach((button) => {
    button.addEventListener("click", () => handleRestrictionSave(button.dataset.restrictionSave));
  });
}

function auditLogOutcomeBadgeClass(outcome) {
  if (outcome === "success") return "is-success";
  if (outcome === "failure") return "is-failure";
  if (outcome === "denied") return "is-denied";
  return "is-gray";
}

function auditLogCategoryBadgeClass(category) {
  if (category === "authentication") return "is-blue";
  if (category === "authorization") return "is-denied";
  if (category === "user_management") return "is-lime";
  if (category === "configuration") return "is-orange";
  if (category === "instance_management") return "is-blue";
  return "is-gray";
}

function renderAuditLog() {
  const tableEl = document.getElementById("audit-log-table");
  const footerEl = document.getElementById("audit-log-footer");
  if (!tableEl || !footerEl) return;

  const al = state.auditLog;

  if (al.loading && !al.initialized) {
    tableEl.innerHTML = `<p class="meta" style="padding:12px 0">Loading audit log…</p>`;
    footerEl.innerHTML = "";
    return;
  }

  if (!al.initialized) {
    tableEl.innerHTML = `<p class="meta" style="padding:12px 0">Select the Security view to load the audit log.</p>`;
    footerEl.innerHTML = "";
    return;
  }

  if (al.events.length === 0) {
    tableEl.innerHTML = `<p class="meta" style="padding:12px 0">No audit events found.</p>`;
    footerEl.innerHTML = "";
    return;
  }

  tableEl.innerHTML = `<div class="audit-log-table">${al.events.map((ev) => {
    const actor = ev.actor || {};
    const resource = ev.resource || {};
    const detail = ev.detail || {};
    const category = ev.category || "";
    const outcome = ev.outcome || "success";

    const actorText = actor.username
      ? `${actor.username}${actor.role ? ` (${actor.role})` : ""}`
      : actor.user_id || "—";
    const ipText = actor.ip || "—";
    const resourceText = resource.name
      ? `${resource.type || ""} ${resource.name}`.trim()
      : resource.id
        ? `${resource.type || ""} ${resource.id}`.trim()
        : "";
    const detailKeys = Object.keys(detail).filter((k) => detail[k] !== null && detail[k] !== undefined);
    const detailText = detailKeys.map((k) => {
      const v = detail[k];
      return `${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`;
    }).join(" · ");

    return `<div class="audit-log-row">
      <div class="audit-log-row-header">
        <div class="audit-log-row-meta">
          <span class="badge ${auditLogCategoryBadgeClass(category)}">${escapeHtml(category || "—")}</span>
          <span class="badge ${auditLogOutcomeBadgeClass(outcome)}">${escapeHtml(outcome)}</span>
          <span style="font-size:13px;font-weight:600">${escapeHtml(ev.event_type || "")}</span>
        </div>
        <span class="meta" style="white-space:nowrap">${escapeHtml(formatDateTime(ev.ts))}</span>
      </div>
      <div class="audit-log-row-body">
        ${actorText !== "—" ? `<div class="audit-log-field"><span class="audit-log-field-label">Actor</span><span class="audit-log-field-value">${escapeHtml(actorText)}</span></div>` : ""}
        ${ipText !== "—" ? `<div class="audit-log-field"><span class="audit-log-field-label">IP</span><span class="audit-log-field-value">${escapeHtml(ipText)}</span></div>` : ""}
        ${resourceText ? `<div class="audit-log-field"><span class="audit-log-field-label">Resource</span><span class="audit-log-field-value">${escapeHtml(resourceText)}</span></div>` : ""}
        ${detailText ? `<div class="audit-log-field"><span class="audit-log-field-label">Detail</span><span class="audit-log-field-value">${escapeHtml(detailText)}</span></div>` : ""}
      </div>
    </div>`;
  }).join("")}</div>`;

  const shown = al.offset + al.events.length;
  const hasMore = shown < al.total;
  footerEl.innerHTML = `<div class="audit-log-footer">
    <span class="meta">Showing ${al.total === 0 ? 0 : al.offset + 1}–${shown} of ${al.total} events</span>
    <div style="display:flex;gap:8px">
      ${al.offset > 0 ? `<button class="btn" onclick="auditLogPrev()">← Prev</button>` : ""}
      ${hasMore ? `<button class="btn" onclick="auditLogNext()">Next →</button>` : ""}
    </div>
  </div>`;
}

async function refreshAuditLog() {
  const al = state.auditLog;
  al.loading = true;
  renderAuditLog();
  try {
    const params = new URLSearchParams({
      limit: al.limit,
      offset: al.offset,
      category: al.category,
      outcome: al.outcome,
      search: al.search,
    });
    const data = await fetchJson(`/admin/auth-audit?${params}`);
    al.events = data.events || [];
    al.total = data.total || 0;
    al.initialized = true;
  } catch (err) {
    setBanner(`Unable to load audit log: ${err.message}`, "error");
  } finally {
    al.loading = false;
    renderAuditLog();
    const catEl = document.getElementById("audit-log-category");
    const outEl = document.getElementById("audit-log-outcome");
    const srchEl = document.getElementById("audit-log-search");
    if (catEl) catEl.value = al.category;
    if (outEl) outEl.value = al.outcome;
    if (srchEl && document.activeElement !== srchEl) srchEl.value = al.search;
  }
}

function onAuditLogFilterChange() {
  const catEl = document.getElementById("audit-log-category");
  const outEl = document.getElementById("audit-log-outcome");
  if (catEl) state.auditLog.category = catEl.value;
  if (outEl) state.auditLog.outcome = outEl.value;
  state.auditLog.offset = 0;
  void refreshAuditLog();
}

function onAuditLogSearchInput() {
  const srchEl = document.getElementById("audit-log-search");
  if (srchEl) state.auditLog.search = srchEl.value;
  state.auditLog.offset = 0;
  clearTimeout(state.auditLog.debounceId);
  state.auditLog.debounceId = setTimeout(() => void refreshAuditLog(), 350);
}

function auditLogNext() {
  const al = state.auditLog;
  al.offset = Math.min(al.offset + al.limit, Math.max(0, al.total - 1));
  void refreshAuditLog();
}

function auditLogPrev() {
  const al = state.auditLog;
  al.offset = Math.max(0, al.offset - al.limit);
  void refreshAuditLog();
}

function setHealth() {
  const healthPill = document.getElementById("health-pill");
  healthPill.textContent = "Operational";
  healthPill.className = "health-pill";
}

function switchView(nextView) {
  state.currentView = safeAuthorizedView(nextView);
  Object.entries(views).forEach(([name, element]) => {
    element.classList.toggle("is-active", name === state.currentView);
  });
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === state.currentView);
  });
  document.getElementById("page-title").textContent =
    state.currentView.charAt(0).toUpperCase() + state.currentView.slice(1);
  syncLocationState();
  if (state.currentView === "security" && !state.auditLog.initialized && !state.auditLog.loading) {
    void refreshAuditLog();
  }
}

async function loadDashboard() {
  if (!state.auth.authenticated) {
    renderAuthShell();
    return;
  }
  try {
    const [schedules, overview, channels, security, activity, accessRequests] = await Promise.all([
      hasAuthPermission("schedule.read") ? fetchJson("/admin/schedules") : Promise.resolve({ jobs: [] }),
      fetchJson("/admin/overview"),
      hasAuthPermission("channel.read") ? fetchJson("/admin/channels") : Promise.resolve({ channels: [] }),
      hasAuthPermission("security.read") ? fetchJson("/admin/security") : Promise.resolve({ findings: [] }),
      hasAuthPermission("activity.read") ? fetchJson("/admin/activity") : Promise.resolve({ events: [], count: 0 }),
      hasAuthPermission("access_request.review") ? fetchJson("/admin/access-requests") : Promise.resolve({ requests: [], count: 0 }),
    ]);
    const runtimeAuditEntries = await Promise.all(
      (hasAuthPermission("runtime_audit.read") ? (overview.instances || []) : []).map(async (instance) => {
        try {
          const runtime = await fetchJson(
            `/admin/runtime-audit?instance_id=${encodeURIComponent(instance.id)}&limit=200&status=all&operation=all`,
          );
          return [instance.id, runtime];
        } catch (_error) {
          return [instance.id, { events: [], summary: { event_count: 0 } }];
        }
      }),
    );
    state.schedules = schedules;
    state.overview = overview;
    state.channels = channels.channels;
    state.security = security;
    state.activity = activity;
    state.accessRequests = accessRequests;
    state.overviewRuntimeAuditByInstance = Object.fromEntries(runtimeAuditEntries);
    if (state.liveInstanceFilter !== "all" && !state.overview.instances.some((instance) => instance.id === state.liveInstanceFilter)) {
      state.liveInstanceFilter = "all";
    }
    if (state.selectedInstanceId && !state.overview.instances.some((instance) => instance.id === state.selectedInstanceId)) {
      state.selectedInstanceId = "";
      state.deleteCandidateId = "";
      state.runtimeAudit.instanceId = "";
      state.runtimeAudit.events = [];
      state.runtimeAudit.summary = null;
      state.runtimeAudit.nextCursor = null;
      state.runtimeAudit.initialized = false;
      state.runtimeAudit.loading = false;
      state.runtimeAudit.loadingMore = false;
      state.runtimeAudit.executionMode = "live";
      state.runtimeAudit.executionPinnedTraceId = "";
      state.runtimeAudit.executionLatestTraceId = "";
      state.runtimeAudit.executionUnreadTraceCount = 0;
      if (state.instanceEditor?.mode === "edit") {
        state.instanceEditor = defaultInstanceEditor();
      }
    }
    const selected = state.overview.instances.find((instance) => instance.id === state.selectedInstanceId);
    if (selected && !state.instanceCreateOpen) {
      const needsHydration =
        !state.instanceEditor
        || state.instanceEditor.mode !== "edit"
        || state.instanceEditor.targetId !== selected.id;
      if (needsHydration) {
        state.instanceEditor = buildInstanceEditorFromInstance(selected);
        state.instanceEditor.runtimeOverrideOpen = isCustomRuntimeConfig(state.instanceEditor);
        void loadInstanceConfig(selected.id);
      }
    }
    renderSummary();
    renderOverviewDashboard();
    renderActivityHeatmap();
    renderInstances();
    renderInstanceForm();
    renderLiveActivity();
    renderLiveSummary();
    renderLiveFilterOptions();
    renderSchedules();
    renderScheduleCreateForm();
    renderProviders();
    renderMcp();
    renderChannels();
    renderSecurityTable();
    renderSecurityControls();
    renderAuditLog();
    renderUsersPanel();
    renderAccountPanel();
    renderNavigation();
    renderUserMenu();
    setHealth();
    syncLocationState();
    clearBanner();
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function refreshActivityOnly() {
  try {
    state.activity = await fetchJson("/admin/activity");
    renderLiveFilterOptions();
    renderLiveActivity();
    renderLiveSummary();
  } catch (error) {
    if (state.currentView === "live") {
      setBanner(`Unable to refresh live activity: ${error.message}`, "error");
    }
  }
}

async function refreshVisibleRealtimePanels() {
  if (!state.auth.authenticated) {
    return;
  }
  if (state.currentView === "live") {
    await refreshActivityOnly();
  }
  if (
    state.currentView === "instances"
    && (state.instanceWorkspaceTab === "runtime-audit" || state.instanceWorkspaceTab === "execution-visualize")
    && state.runtimeAudit.autoRefresh
  ) {
    await refreshRuntimeAuditExplorer({ append: false, silent: true });
  }
}

async function handleChannelSave(key) {
  const [instanceId, channelName] = key.split(":");
  const enabled = Array.from(document.querySelectorAll("[data-channel-enabled]"))
    .find((element) => element.dataset.channelEnabled === key)?.checked ?? false;
  const allowText = Array.from(document.querySelectorAll("[data-channel-allow]"))
    .find((element) => element.dataset.channelAllow === key)?.value ?? "";
  const settings = {};
  document.querySelectorAll("[data-channel-setting]").forEach((element) => {
    if (element.dataset.channelSetting !== key) return;
    const settingKey = element.dataset.settingKey;
    if (!settingKey) return;
    if (element.type === "checkbox") {
      settings[settingKey] = element.checked;
      return;
    }
    const value = element.value ?? "";
    settings[settingKey] = element.dataset.nullable === "true" && value.trim() === "" ? null : value;
  });
  const allowFrom = allowText
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);

  state.busyKey = key;
  renderInstanceWorkspaceContent();
  try {
    const result = await patchJson(`/admin/channels/${channelName}`, {
      instance_id: instanceId,
      enabled,
      allow_from: allowFrom,
      settings,
    });
    await runAutoLifecycleAfterSave(result.instance || null);
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to save channel '${channelName}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderInstanceWorkspaceContent();
  }
}

async function handleAccessRequestApprove({ instanceId, channelName, senderId }) {
  if (!instanceId || !channelName || !senderId) return;
  const busyKey = `approve:${instanceId}:${channelName}:${senderId}`;
  state.busyKey = busyKey;
  renderInstanceWorkspaceContent();
  try {
    const result = await postJson("/admin/access-requests/approve", {
      instance_id: instanceId,
      channel_name: channelName,
      sender_id: senderId,
    });
    if (result.runtime?.applied) {
      clearBanner();
    } else {
      setBanner(
        `Accepted sender '${senderId}', but runtime apply was not completed: ${result.runtime?.detail || "unknown reason"}`,
        "warning",
      );
    }
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to accept sender '${senderId}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderInstanceWorkspaceContent();
  }
}

async function handleAccessRequestReject({ instanceId, channelName, senderId }) {
  if (!instanceId || !channelName || !senderId) return;
  const busyKey = `reject:${instanceId}:${channelName}:${senderId}`;
  state.busyKey = busyKey;
  renderInstanceWorkspaceContent();
  try {
    await postJson("/admin/access-requests/reject", {
      instance_id: instanceId,
      channel_name: channelName,
      sender_id: senderId,
    });
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to reject sender '${senderId}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderInstanceWorkspaceContent();
  }
}

async function handleRestrictionSave(instanceId) {
  const key = `restriction:${instanceId}`;
  const checked = document.querySelector(`[data-restriction-toggle="${CSS.escape(instanceId)}"]`)?.checked ?? false;

  state.busyKey = key;
  renderSecurityControls();
  try {
    await patchJson("/admin/security/workspace-restriction", {
      instance_id: instanceId,
      restrict_to_workspace: checked,
    });
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to update workspace restriction: ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderSecurityControls();
  }
}

async function handleRuntimePolicySave(instanceId) {
  const key = `runtime-policy:${instanceId}`;
  const instance = state.overview.instances.find((item) => item.id === instanceId);
  if (!instance) return;
  const runtimeMode = document.querySelector(`[data-runtime-mode="${CSS.escape(instanceId)}"]`)?.value || instance.runtime_config?.mode || "host";
  const sandboxNetworkPolicy = document.querySelector(`[data-runtime-network="${CSS.escape(instanceId)}"]`)?.value || instance.runtime_config?.sandbox?.networkPolicy || "default";
  const sandboxExecutionStrategy = document.querySelector(`[data-runtime-strategy="${CSS.escape(instanceId)}"]`)?.value || instance.runtime_config?.sandbox?.executionStrategy || "persistent";
  const sandboxProfile = document.querySelector(`[data-runtime-profile="${CSS.escape(instanceId)}"]`)?.value || instance.runtime_config?.sandbox?.profile || "balanced";

  state.busyKey = key;
  renderSecurityControls();
  renderInstanceWorkspaceContent();
  try {
    await patchJson(`/admin/instances/${instanceId}`, {
      runtime_mode: runtimeMode,
      sandbox_profile: sandboxProfile,
      sandbox_image: instance.runtime_config?.sandbox?.image || "softnixclaw:latest",
      sandbox_execution_strategy: sandboxExecutionStrategy,
      sandbox_cpu_limit: instance.runtime_config?.sandbox?.cpuLimit || "",
      sandbox_memory_limit: instance.runtime_config?.sandbox?.memoryLimit || "",
      sandbox_pids_limit: instance.runtime_config?.sandbox?.pidsLimit || 256,
      sandbox_tmpfs_size_mb: instance.runtime_config?.sandbox?.tmpfsSizeMb || 128,
      sandbox_network_policy: sandboxNetworkPolicy,
      sandbox_timeout_seconds: instance.runtime_config?.sandbox?.timeoutSeconds || 30,
    });
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to update runtime policy: ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderSecurityControls();
    renderInstanceWorkspaceContent();
  }
}

async function handleInstanceAction(key) {
  const [, instanceId, action] = key.split(":");
  if (!window.confirm(`Run '${action}' for instance '${instanceId}'?`)) {
    return;
  }
  state.busyKey = key;
  renderInstances();
  try {
    const result = await postJson(`/admin/instances/${instanceId}/${action}`, {});
    if (result.ok) {
      clearBanner();
    } else {
      setBanner(summarizeCommandResult(result), "error");
    }
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to ${action} instance '${instanceId}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderInstances();
  }
}

function resolveAutoLifecycleAction(instance) {
  const runtime = instance?.runtime || {};
  const actions = Array.isArray(runtime.actions) ? runtime.actions : [];
  if (!runtime.manageable || actions.length === 0) {
    return "";
  }
  const status = String(runtime.status || "").toLowerCase();
  if (status === "running") {
    if (actions.includes("restart")) return "restart";
    if (actions.includes("start")) return "start";
    return "";
  }
  if (actions.includes("start")) return "start";
  if (actions.includes("restart")) return "restart";
  return "";
}

async function runAutoLifecycleAfterSave(instance) {
  const instanceId = instance?.id;
  const action = resolveAutoLifecycleAction(instance);
  if (!instanceId || !action) {
    return;
  }
  const runtimeStatus = String(instance?.runtime?.status || "").toLowerCase();
  if (action === "start" && runtimeStatus !== "running") {
    const label = instance?.name || instanceId;
    const confirmed = window.confirm(`Instance '${label}' is stopped. Start it now?`);
    if (!confirmed) {
      return;
    }
  }
  const result = await postJson(`/admin/instances/${instanceId}/${action}`, {});
  if (!result.ok) {
    throw new Error(summarizeCommandResult(result));
  }
}

async function handleInstanceFormSubmit() {
  const editor = state.instanceEditor || defaultInstanceEditor();
  const derived = deriveInstanceValues(editor);
  const instanceId = derived.instanceId;
  const sourceConfig = derived.sourceConfig;
  const targetConfigPath = `${inferSoftnixHome()}/instances/${instanceId.trim() || "<instance-id>"}/config.json`;
  if (!instanceId.trim()) {
    setBanner("Instance ID must not be empty.", "error");
    return;
  }
  if (editor.mode === "create" && state.overview.instances.some((instance) => instance.id === instanceId)) {
    setBanner(`Instance ID '${instanceId}' already exists.`, "error");
    return;
  }
  if (editor.mode === "create" && normalizePath(sourceConfig) && normalizePath(sourceConfig) === normalizePath(targetConfigPath)) {
    setBanner("Source Config must not be the same as the generated target config path.", "error");
    return;
  }
  const gatewayPortText = (editor.gatewayPort || "").trim();
  if (gatewayPortText && (!/^\d+$/.test(gatewayPortText) || Number(gatewayPortText) < 1 || Number(gatewayPortText) > 65535)) {
    setBanner("Gateway Port must be a number between 1 and 65535.", "error");
    return;
  }
  const sandboxTimeoutText = (editor.sandboxTimeoutSeconds || "").trim();
  if (sandboxTimeoutText && (!/^\d+$/.test(sandboxTimeoutText) || Number(sandboxTimeoutText) < 1)) {
    setBanner("Sandbox Stop Timeout must be a positive integer.", "error");
    return;
  }
  const sandboxPidsText = (editor.sandboxPidsLimit || "").trim();
  if (sandboxPidsText && (!/^\d+$/.test(sandboxPidsText) || Number(sandboxPidsText) < 1)) {
    setBanner("Sandbox PIDs Limit must be a positive integer.", "error");
    return;
  }
  const sandboxTmpfsText = (editor.sandboxTmpfsSizeMb || "").trim();
  if (sandboxTmpfsText && (!/^\d+$/.test(sandboxTmpfsText) || Number(sandboxTmpfsText) < 1)) {
    setBanner("Sandbox tmpfs Size must be a positive integer.", "error");
    return;
  }

  const payload = {
    instance_id: instanceId,
    name: document.getElementById("instance-form-name")?.value || "",
    owner: derived.owner,
    env: derived.env,
    repo_root: editor.repoRoot || "/Volumes/Seagate/myapp/nanobot",
    nanobot_bin: editor.nanobotBin || "/opt/anaconda3/bin/nanobot",
    gateway_port: gatewayPortText ? Number(gatewayPortText) : null,
    source_config: sourceConfig,
    runtime_mode: editor.runtimeMode || "sandbox",
    sandbox_profile: normalizeSandboxProfile(editor.sandboxProfile || "balanced"),
    sandbox_image: editor.sandboxImage || "softnixclaw:latest",
    sandbox_execution_strategy: editor.sandboxExecutionStrategy || "persistent",
    sandbox_cpu_limit: editor.sandboxCpuLimit || "",
    sandbox_memory_limit: editor.sandboxMemoryLimit || "",
    sandbox_pids_limit: sandboxPidsText ? Number(sandboxPidsText) : 256,
    sandbox_tmpfs_size_mb: sandboxTmpfsText ? Number(sandboxTmpfsText) : 128,
    sandbox_network_policy: editor.sandboxNetworkPolicy || "default",
    sandbox_timeout_seconds: sandboxTimeoutText ? Number(sandboxTimeoutText) : 30,
  };

  state.busyKey = "instance-form";
  renderInstances();
  renderInstanceForm();
  try {
    let savedInstance = null;
    if (editor.mode === "edit") {
      const result = await patchJson(`/admin/instances/${editor.targetId}`, payload);
      savedInstance = result.instance || null;
      clearBanner();
    } else {
      const result = await postJson("/admin/instances", payload);
      savedInstance = result.instance || null;
      clearBanner();
      state.selectedInstanceId = payload.instance_id;
      state.instanceCreateOpen = false;
    }
    await runAutoLifecycleAfterSave(savedInstance);
    state.instanceEditor = defaultInstanceEditor();
    state.deleteCandidateId = "";
    state.instanceWorkspaceTab = "manage";
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to save instance: ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderInstances();
    renderInstanceForm();
  }
}

async function handleInstanceDelete(instanceId) {
  const instance = state.overview.instances.find((item) => item.id === instanceId);
  if (!instance) {
    setBanner(`Instance '${instanceId}' was not found. Refresh the dashboard and try again.`, "error");
    return;
  }
  state.selectedInstanceId = instanceId;
  state.instanceCreateOpen = false;
  state.deleteCandidateId = instanceId;
  state.instanceWorkspaceTab = "manage";
  ensureRuntimeAuditState(instanceId);
  state.instanceEditor = buildInstanceEditorFromInstance(instance);
  state.instanceEditor.runtimeOverrideOpen = isCustomRuntimeConfig(state.instanceEditor);
  renderInstances();
  syncLocationState();
  const confirmButton = document.getElementById("instance-delete-confirm");
  if (confirmButton) {
    confirmButton.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

async function confirmInstanceDelete() {
  const instanceId = state.deleteCandidateId;
  if (!instanceId) {
    return;
  }
  state.busyKey = "instance-form";
  renderInstances();
  renderInstanceForm();
  try {
    await deleteJson(`/admin/instances/${instanceId}`, { purge_files: true });
    clearBanner();
    state.instanceEditor = defaultInstanceEditor();
    state.selectedInstanceId = "";
    state.deleteCandidateId = "";
    state.instanceWorkspaceTab = "manage";
    state.skillsByInstance = {};
    state.runtimeAudit.instanceId = "";
    state.runtimeAudit.events = [];
    state.runtimeAudit.summary = null;
    state.runtimeAudit.nextCursor = null;
    state.runtimeAudit.initialized = false;
    state.runtimeAudit.loading = false;
    state.runtimeAudit.loadingMore = false;
    state.runtimeAudit.executionMode = "live";
    state.runtimeAudit.executionPinnedTraceId = "";
    state.runtimeAudit.executionLatestTraceId = "";
    state.runtimeAudit.executionUnreadTraceCount = 0;
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to delete instance '${instanceId}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderInstances();
    renderInstanceForm();
  }
}

function cancelInstanceDelete() {
  state.deleteCandidateId = "";
  renderInstanceForm();
  renderInstances();
  syncLocationState();
}

function schedulePayloadFromForm() {
  const kind = document.getElementById("schedule-kind")?.value || "every";
  return {
    instance_id: document.getElementById("schedule-instance")?.value || "default",
    name: document.getElementById("schedule-name")?.value || "",
    schedule: {
      kind,
      every_ms: kind === "every" ? Number(document.getElementById("schedule-every-ms")?.value || "0") : null,
      expr: kind === "cron" ? document.getElementById("schedule-cron-expr")?.value || "" : null,
      tz: kind === "cron" ? document.getElementById("schedule-cron-tz")?.value || "" : null,
      at_ms: kind === "at" ? Number(document.getElementById("schedule-at-ms")?.value || "0") : null,
    },
    message: document.getElementById("schedule-message")?.value || "",
    deliver: document.getElementById("schedule-deliver")?.checked || false,
    channel: document.getElementById("schedule-channel")?.value || "",
    to: document.getElementById("schedule-to")?.value || "",
    delete_after_run: document.getElementById("schedule-delete-after-run")?.checked || false,
  };
}

async function handleScheduleCreate() {
  state.busyKey = "schedule-create";
  renderScheduleCreateForm();
  try {
    const payload = schedulePayloadFromForm();
    await postJson("/admin/schedules", payload);
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to create schedule: ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderScheduleCreateForm();
  }
}

async function handleScheduleToggle(key) {
  const [, instanceId, jobId] = key.split(":");
  const instance = state.schedules.instances.find((item) => item.instance_id === instanceId);
  const job = instance?.jobs.find((item) => item.id === jobId);
  const nextEnabled = !job?.enabled;
  state.busyKey = key;
  renderSchedules();
  try {
    await patchJson(`/admin/schedules/${jobId}/enabled`, {
      instance_id: instanceId,
      enabled: nextEnabled,
    });
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to update schedule '${jobId}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderSchedules();
  }
}

async function handleScheduleRun(key) {
  const [, instanceId, jobId] = key.split(":");
  state.busyKey = key;
  renderSchedules();
  try {
    await postJson(`/admin/schedules/${jobId}/run`, { instance_id: instanceId });
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to run schedule '${jobId}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderSchedules();
  }
}

async function handleScheduleDelete(key) {
  const [, instanceId, jobId] = key.split(":");
  if (!window.confirm(`Delete schedule '${jobId}' from instance '${instanceId}'?`)) {
    return;
  }
  state.busyKey = key;
  renderSchedules();
  try {
    await deleteJson(`/admin/schedules/${jobId}`, { instance_id: instanceId });
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to delete schedule '${jobId}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderSchedules();
  }
}

function parseJsonField(selector, fallback) {
  const text = document.querySelector(selector)?.value ?? "";
  if (!text.trim()) {
    return fallback;
  }
  return JSON.parse(text);
}

async function handleProviderDefaultsSave(instanceId) {
  const key = `provider-default:${instanceId}`;
  const model = document.querySelector(`[data-default-model="${CSS.escape(instanceId)}"]`)?.value ?? "";
  const provider = document.querySelector(`[data-default-provider="${CSS.escape(instanceId)}"]`)?.value ?? "auto";
  state.busyKey = key;
  renderProviders();
  try {
    const result = await patchJson("/admin/providers/default", {
      instance_id: instanceId,
      model,
      provider,
    });
    await runAutoLifecycleAfterSave(result.instance || null);
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to update provider defaults: ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderProviders();
  }
}

async function handleProviderSave(key) {
  const [, instanceId, providerName] = key.split(":");
  state.busyKey = key;
  renderProviders();
  try {
    const extra_headers = parseJsonField(`[data-provider-headers="${CSS.escape(key)}"]`, {});
    const result = await patchJson(`/admin/providers/${providerName}`, {
      instance_id: instanceId,
      api_key: document.querySelector(`[data-provider-key="${CSS.escape(key)}"]`)?.value ?? "",
      api_base: document.querySelector(`[data-provider-base="${CSS.escape(key)}"]`)?.value ?? "",
      extra_headers,
    });
    await runAutoLifecycleAfterSave(result.instance || null);
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to update provider '${providerName}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderProviders();
  }
}

function formatValidationMessage(label, result) {
  const details = (result.findings || []).map((item) => item.detail).join(" ");
  if (details) {
    return `${label} validation: ${result.status}. ${details}`;
  }
  return `${label} validation: ${result.status}. No findings.`;
}

async function handleProviderValidate(key) {
  const [, instanceId, providerName] = key.split(":");
  state.busyKey = key;
  renderProviders();
  try {
    const result = await postJson(`/admin/providers/${providerName}/validate`, {
      instance_id: instanceId,
    });
    setBanner(formatValidationMessage(`Provider '${providerName}'`, result), result.status === "error" ? "error" : "warning");
  } catch (error) {
    setBanner(`Unable to validate provider '${providerName}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderProviders();
  }
}

function buildMcpPayload(baseKey, instanceId, serverName) {
  return {
    instance_id: instanceId,
    server_name: serverName,
    server: {
      type: document.querySelector(`[data-mcp-type="${CSS.escape(baseKey)}"]`)?.value || null,
      command: document.querySelector(`[data-mcp-command="${CSS.escape(baseKey)}"]`)?.value ?? "",
      url: document.querySelector(`[data-mcp-url="${CSS.escape(baseKey)}"]`)?.value ?? "",
      tool_timeout: Number(document.querySelector(`[data-mcp-timeout="${CSS.escape(baseKey)}"]`)?.value || "30"),
      args: parseJsonField(`[data-mcp-args="${CSS.escape(baseKey)}"]`, []),
      headers: parseJsonField(`[data-mcp-headers="${CSS.escape(baseKey)}"]`, {}),
    },
  };
}

async function handleMcpSave(key) {
  const [, instanceId, serverName] = key.split(":");
  state.busyKey = key;
  renderMcp();
  try {
    const result = await patchJson("/admin/mcp/servers", buildMcpPayload(key, instanceId, serverName));
    await runAutoLifecycleAfterSave(result.instance || null);
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to update MCP server '${serverName}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderMcp();
  }
}

async function handleMcpValidate(key) {
  const [, instanceId, serverName] = key.split(":");
  state.busyKey = key;
  renderMcp();
  try {
    const result = await postJson(`/admin/mcp/servers/${serverName}/validate`, {
      instance_id: instanceId,
    });
    setBanner(formatValidationMessage(`MCP server '${serverName}'`, result), result.status === "error" ? "error" : "warning");
  } catch (error) {
    setBanner(`Unable to validate MCP server '${serverName}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderMcp();
  }
}

async function handleMcpDelete(key) {
  const [, instanceId, serverName] = key.split(":");
  if (!window.confirm(`Delete MCP server '${serverName}' from instance '${instanceId}'?`)) {
    return;
  }
  state.busyKey = key;
  renderMcp();
  try {
    await deleteJson(`/admin/mcp/servers/${serverName}`, { instance_id: instanceId });
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to delete MCP server '${serverName}': ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderMcp();
  }
}

async function handleMcpCreate(instanceId) {
  const key = `mcp-create:${instanceId}`;
  state.busyKey = key;
  renderMcp();
  try {
    const serverName = document.querySelector(`[data-mcp-create-name="${CSS.escape(instanceId)}"]`)?.value ?? "";
    const payload = {
      instance_id: instanceId,
      server_name: serverName,
      server: {
        type: document.querySelector(`[data-mcp-create-type="${CSS.escape(instanceId)}"]`)?.value || null,
        command: document.querySelector(`[data-mcp-create-command="${CSS.escape(instanceId)}"]`)?.value ?? "",
        url: document.querySelector(`[data-mcp-create-url="${CSS.escape(instanceId)}"]`)?.value ?? "",
        tool_timeout: Number(document.querySelector(`[data-mcp-create-timeout="${CSS.escape(instanceId)}"]`)?.value || "30"),
        args: parseJsonField(`[data-mcp-create-args="${CSS.escape(instanceId)}"]`, []),
        headers: parseJsonField(`[data-mcp-create-headers="${CSS.escape(instanceId)}"]`, {}),
      },
    };
    const result = await patchJson("/admin/mcp/servers", payload);
    await runAutoLifecycleAfterSave(result.instance || null);
    state.mcpCreateOpenByInstance[instanceId] = false;
    clearBanner();
    await loadDashboard();
  } catch (error) {
    setBanner(`Unable to add MCP server: ${error.message}`, "error");
  } finally {
    state.busyKey = "";
    renderMcp();
  }
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});

document.getElementById("refresh-button").addEventListener("click", () => {
  void loadDashboard();
});
document.getElementById("live-instance-filter")?.addEventListener("change", (event) => {
  state.liveInstanceFilter = event.target.value || "all";
  renderLiveActivity();
  renderLiveSummary();
  syncLocationState();
});

async function initializeApp() {
  restoreLocationState();
  await loadAuthState();
  switchView(state.auth.authenticated ? state.currentView : "overview");
  if (state.auth.authenticated) {
    await loadDashboard();
  }
}

void initializeApp();
window.setInterval(() => {
  void refreshVisibleRealtimePanels();
}, 5000);
