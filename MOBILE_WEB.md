# Softnix Mobile Web Application — Development Plan

## Project Overview

พัฒนา **Mobile Web App** ที่ใช้งานผ่าน browser บน smartphone แทน Native Mobile App
โดยให้ผู้ใช้สแกน QR Code เพื่อ register device แล้วเชื่อมต่อกับ Nanobot Agent ผ่าน softnix_app channel

## Repository Context

- **Repo root**: `/Volumes/Seagate/myapp/nanobot/`
- **Admin server** (Python `http.server.ThreadingHTTPServer`): port 18880
- **Static files**: `nanobot/admin/static/`
- **Gateway/Agent** (per-instance Docker container): port varies (e.g., 18792 for bigbike2-prod)
- **Instance config**: `~/.softnix/instances/{id}/config.json`
- **Admin security dir**: `~/.softnix/admin/security/` (contains `users.json`, `sessions.json`)

---

## Existing System to Understand Before Coding

### softnix_app Channel
- **File**: `nanobot/channels/softnix_app.py`
- File-based relay channel: polls `inbound.jsonl` every 1 second, writes replies to `outbound.jsonl`
- Relay path: `~/.softnix/instances/{id}/workspace/mobile_relay/`
- `allow_from` list in config gates which `sender_id` values are accepted (via `BaseChannel.is_allowed()` in `nanobot/channels/base.py`)

### Existing Mobile API Endpoints (all in `nanobot/admin/server.py` + `service.py`)
```
POST /admin/mobile/pair      → generate pairing token (currently NOT validated on register)
POST /admin/mobile/register  → add device_id to config.channels.softnix_app.allow_from
POST /admin/mobile/message   → write to inbound.jsonl
GET  /admin/mobile/poll      → read from outbound.jsonl, filter by sender_id
```
All 4 endpoints bypass admin session auth (line 696 in server.py skips `_authorize_user_mutation` for `/admin/mobile/`)

### Admin Auth Store
- **File**: `nanobot/admin/auth_store.py`
- Class `AdminAuthStore` — stores users, sessions in `~/.softnix/admin/security/`
- Pattern: `_load_json(path, default)` and `_save_json(path, payload)` for atomic file reads/writes
- Available helpers: `iso_now()`, `is_session_expired()`, `iso_in()` from `nanobot/admin/auth.py`

### Admin UI
- **Files**: `nanobot/admin/static/app.js`, `index.html`, `styles.css`
- Single-page app (vanilla JS, no framework)
- softnix_app channel rendered in channels editor — currently shows allow_from as textarea
- Existing UI patterns: `.item-card`, `.field`, `.badge`, `.inline-actions`, `.modal-overlay`, `.modal-box`
- `new_csrf_token()` available in service.py for generating random tokens

---

## What to Build

### 1. New Files to Create

#### `nanobot/admin/static/mobile/index.html`
Mobile SPA shell. Standalone page (NOT related to `index.html` admin console).
```html
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <link rel="manifest" href="/mobile/manifest.json">
  <link rel="stylesheet" href="/mobile/mobile.css">
  <title>Softnix Agent</title>
</head>
<body>
  <!-- 3 screens toggled by JS: #screen-pairing, #screen-chat, #screen-error -->
  <script src="/mobile/mobile.js"></script>
</body>
</html>
```

#### `nanobot/admin/static/mobile/mobile.js`
Vanilla JS (~500 lines). Key responsibilities:
- **init()**: check localStorage → route to pairing screen or chat screen
- **register(instance_id, token)**: POST /admin/mobile/register → save device to localStorage
- **sendMessage(text)**: POST /admin/mobile/message, optimistic UI append
- **startPolling()**: `setInterval(pollReplies, 2000)`
- **pollReplies()**: GET /admin/mobile/poll → append agent bubbles → trigger notification if backgrounded
- **requestNotificationPermission()**: called inside first Send click (user gesture required)

localStorage key: `softnix_mobile_v1`
```json
{
  "device_id": "mob-<uuid>",
  "instance_id": "bigbike2-prod",
  "label": "iPhone 15",
  "registered_at": "2026-03-14T10:00:00Z"
}
```

Notification logic:
```javascript
if (document.visibilityState === 'hidden' && Notification.permission === 'granted') {
  new Notification('Softnix Agent', {
    body: reply.text.slice(0, 100),
    icon: '/static/Logo_Softnix.png',
    tag: 'softnix-reply',  // replaces previous notification
    renotify: true
  });
}
```

#### `nanobot/admin/static/mobile/mobile.css`
Chat bubble UI, native mobile feel:
- Fixed header + fixed compose bar at bottom, scrollable `#messages` in between
- `.msg--user` bubbles right-aligned, `.msg--agent` bubbles left-aligned
- `body { overscroll-behavior: none }` — prevent pull-to-refresh
- `@media (prefers-color-scheme: dark)` dark mode support
- Brand colors match admin UI CSS vars where available

#### `nanobot/admin/static/mobile/manifest.json`
```json
{
  "name": "Softnix Agent",
  "short_name": "Softnix",
  "start_url": "/mobile",
  "display": "standalone",
  "background_color": "#0f172a",
  "theme_color": "#6366f1",
  "icons": [{"src": "/static/Logo_Softnix.png", "sizes": "192x192", "type": "image/png"}]
}
```

---

### 2. Files to Modify

#### `nanobot/admin/auth_store.py`

Add to `__init__`:
```python
self.pairing_tokens_path = self.security_dir / "mobile_pairing_tokens.json"
self.mobile_devices_path = self.security_dir / "mobile_devices.json"
```

Add these methods (follow existing `_load_json`/`_save_json` pattern):

```python
def create_pairing_token(self, instance_id: str, token: str, expires_at: str) -> None:
    """Store a pairing token. Prune expired tokens first."""
    payload = self._load_json(self.pairing_tokens_path, {"tokens": []})
    # Prune expired/used
    payload["tokens"] = [t for t in payload["tokens"]
                         if not t.get("used") and not is_session_expired(t["expires_at"])]
    payload["tokens"].append({
        "token": token,
        "instance_id": instance_id,
        "expires_at": expires_at,
        "used": False
    })
    self._save_json(self.pairing_tokens_path, payload)

def validate_and_consume_pairing_token(self, instance_id: str, token: str) -> bool:
    """Validate token (not used, not expired, correct instance). Mark used if valid."""
    payload = self._load_json(self.pairing_tokens_path, {"tokens": []})
    for t in payload["tokens"]:
        if t["token"] == token and t["instance_id"] == instance_id:
            if t.get("used") or is_session_expired(t["expires_at"]):
                return False
            t["used"] = True
            self._save_json(self.pairing_tokens_path, payload)
            return True
    return False

def list_mobile_devices(self, instance_id: str) -> list[dict]:
    payload = self._load_json(self.mobile_devices_path, {"devices": []})
    return [d for d in payload["devices"] if d["instance_id"] == instance_id]

def upsert_mobile_device(self, instance_id: str, device_id: str, label: str) -> None:
    payload = self._load_json(self.mobile_devices_path, {"devices": []})
    for d in payload["devices"]:
        if d["device_id"] == device_id and d["instance_id"] == instance_id:
            d["label"] = label
            d["last_seen"] = iso_now()
            self._save_json(self.mobile_devices_path, payload)
            return
    payload["devices"].append({
        "device_id": device_id,
        "instance_id": instance_id,
        "label": label,
        "registered_at": iso_now(),
        "last_seen": iso_now()
    })
    self._save_json(self.mobile_devices_path, payload)

def delete_mobile_device(self, instance_id: str, device_id: str) -> None:
    payload = self._load_json(self.mobile_devices_path, {"devices": []})
    payload["devices"] = [d for d in payload["devices"]
                          if not (d["device_id"] == device_id and d["instance_id"] == instance_id)]
    self._save_json(self.mobile_devices_path, payload)

def update_device_last_seen(self, instance_id: str, device_id: str) -> None:
    payload = self._load_json(self.mobile_devices_path, {"devices": []})
    for d in payload["devices"]:
        if d["device_id"] == device_id and d["instance_id"] == instance_id:
            d["last_seen"] = iso_now()
            self._save_json(self.mobile_devices_path, payload)
            return
```

#### `nanobot/admin/service.py`

**Modify `get_mobile_pairing_data`** (currently line ~232):
```python
# After generating pairing_token, store it:
self.auth_store.create_pairing_token(
    instance_id=instance_id,
    token=pairing_token,
    expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
)
```

**Modify `register_mobile_client`** (currently line ~299) — add `pairing_token` param + validation:
```python
def register_mobile_client(self, instance_id: str, device_id: str, pairing_token: str | None = None, label: str = "") -> dict:
    if pairing_token:
        if not self.auth_store.validate_and_consume_pairing_token(instance_id, pairing_token):
            raise PermissionError("Invalid or expired pairing token")
    # ... existing allow_from logic ...
    self.auth_store.upsert_mobile_device(instance_id, device_id, label or device_id)
    return {"status": "registered", "new": ...}
```

**Modify `get_mobile_replies`** — add last_seen update:
```python
if all_replies:
    self.auth_store.update_device_last_seen(instance_id, sender_id)
```

**Add new methods**:
```python
def list_mobile_devices(self, instance_id: str) -> list[dict]:
    return self.auth_store.list_mobile_devices(instance_id)

def delete_mobile_device(self, instance_id: str, device_id: str) -> dict:
    # Remove from auth_store
    self.auth_store.delete_mobile_device(instance_id, device_id)
    # Remove from config.channels.softnix_app.allow_from
    target = next((t for t in self._load_targets() if t.id == instance_id), None)
    if not target:
        raise ValueError(f"Instance '{instance_id}' not found")
    config = self._load_target_config(target)
    if device_id in config.channels.softnix_app.allow_from:
        config.channels.softnix_app.allow_from.remove(device_id)
        self.update_instance_config(instance_id=instance_id, config_data=config.model_dump(by_alias=True))
    return {"status": "deleted"}
```

#### `nanobot/admin/server.py`

**Add mobile static serving** in `resolve_static_asset()` (after existing logo entries, before the final `return "", ""`):
```python
# Mobile web app
if raw_path == "/mobile" or raw_path.startswith("/mobile/"):
    subpath = raw_path[len("/mobile"):].lstrip("/") or "index.html"
    asset = STATIC_DIR / "mobile" / subpath
    if asset.exists() and asset.is_file():
        ext = asset.suffix.lstrip(".")
        ct = {"html": "text/html", "js": "application/javascript", "css": "text/css",
              "json": "application/json", "png": "image/png"}.get(ext, "application/octet-stream")
        return asset, f"{ct}; charset=utf-8" if ext != "png" else ct
```

**Add GET `/admin/mobile/devices`** in `resolve_admin_get()` (near other mobile routes, line ~160):
```python
if path == "/admin/mobile/devices":
    instance_id = (query.get("instance_id") or [None])[0]
    if not instance_id:
        return HTTPStatus.BAD_REQUEST, {"error": "Missing instance_id"}
    return HTTPStatus.OK, {"devices": service.list_mobile_devices(instance_id)}
```

**Add DELETE `/admin/mobile/devices/<device_id>`** in `do_DELETE` handler:
```python
# In resolve_admin_delete() or inside do_DELETE:
import re
m = re.match(r"^/admin/mobile/devices/(.+)$", path)
if m:
    device_id = m.group(1)
    instance_id = payload.get("instance_id")
    if not instance_id:
        return HTTPStatus.BAD_REQUEST, {"error": "instance_id required"}
    return HTTPStatus.OK, service.delete_mobile_device(instance_id, device_id)
```

**Update register endpoint** to pass `pairing_token` and `label`:
```python
if path == "/admin/mobile/register":
    pairing_token = payload.get("pairing_token")
    label = payload.get("label", "")
    # service raises PermissionError if token invalid
    return HTTPStatus.OK, service.register_mobile_client(instance_id, device_id, pairing_token, label)
# Catch PermissionError → HTTPStatus.FORBIDDEN
```

**Auth for `/admin/mobile/pair`**: move out of the unauthenticated block so admin session is required (only admin generates QR)

**Handle PermissionError** in `do_POST` exception handler → return 403

#### `nanobot/admin/static/index.html`

Add QR modal (alongside existing `user-modal`):
```html
<div id="qr-modal" class="modal-overlay is-hidden" role="dialog" aria-modal="true">
  <div class="modal-box" style="max-width:380px; text-align:center">
    <div class="modal-header">
      <h3>Pair Mobile Device</h3>
      <button class="modal-close" id="qr-modal-close">&times;</button>
    </div>
    <div id="qr-modal-body">
      <!-- Dynamically filled by app.js -->
    </div>
  </div>
</div>
```

#### `nanobot/admin/static/app.js`

**Add state**:
```javascript
state.mobileDevicesByInstance = {};   // { [instanceId]: device[] }
state.qrModal = { open: false, instanceId: null, countdownTimer: null };
```

**Modify `renderSelectedInstanceChannels()`** (line ~1591) and `renderChannels()` (line ~4367).
When `channel.name === "softnix_app"`:

Replace allow_from textarea block with:
```javascript
const devices = state.mobileDevicesByInstance[instance.id] || [];
const deviceCards = devices.length === 0
  ? `<p class="meta" style="padding:8px 0">No devices registered yet.</p>`
  : devices.map(d => `
    <div class="mobile-device-card">
      <div>
        <div class="table-primary">${escapeHtml(d.label || d.device_id)}</div>
        <div class="table-secondary">
          <span class="meta-label">ID:</span> ${escapeHtml(d.device_id)}
          &nbsp;·&nbsp; Last seen: ${escapeHtml(d.last_seen ? new Date(d.last_seen).toLocaleString() : "Never")}
        </div>
      </div>
      <button class="secondary-button is-small is-danger"
        data-mobile-delete="${escapeHtml(d.device_id)}"
        data-mobile-instance="${escapeHtml(instance.id)}">Delete</button>
    </div>`).join("");

return `
  <div class="field">
    <div class="row-between" style="align-items:center; margin-bottom:8px">
      <label>Registered Devices</label>
      <button class="primary-button is-small" data-mobile-pair="${escapeHtml(instance.id)}">+ Pair Device</button>
    </div>
    <div class="stack">${deviceCards}</div>
  </div>`;
```

**Add functions**:

```javascript
async function loadMobileDevices(instanceId) {
  const data = await apiFetch(`/admin/mobile/devices?instance_id=${encodeURIComponent(instanceId)}`);
  state.mobileDevicesByInstance[instanceId] = data?.devices || [];
}

function loadQRCodeLibrary() {
  return new Promise((resolve) => {
    if (window.QRCode) return resolve();
    const s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/qrcode@1.5.4/build/qrcode.min.js";
    s.onload = resolve;
    document.head.appendChild(s);
  });
}

async function openQRModal(instanceId) {
  await loadQRCodeLibrary();
  const data = await apiFetch("/admin/mobile/pair", {
    method: "POST",
    body: JSON.stringify({ instance_id: instanceId })
  });
  if (!data?.pairing_token) return showToast("Failed to generate QR code", "error");

  state.qrModal = { open: true, instanceId, data };
  document.getElementById("qr-modal").classList.remove("is-hidden");
  renderQRModalBody(instanceId, data);
}

function renderQRModalBody(instanceId, data) {
  const url = `${window.location.origin}/mobile?instance_id=${encodeURIComponent(instanceId)}&token=${encodeURIComponent(data.pairing_token)}`;
  const body = document.getElementById("qr-modal-body");
  body.innerHTML = `
    <div class="qr-canvas-wrap"><canvas id="qr-canvas"></canvas></div>
    <p class="qr-instructions">Scan with your phone camera to open Softnix Agent</p>
    <p class="qr-expiry" id="qr-countdown"></p>
    <button class="secondary-button is-small" id="qr-copy-btn">Copy Link</button>`;

  QRCode.toCanvas(document.getElementById("qr-canvas"), url, { width: 240, margin: 2 });

  document.getElementById("qr-copy-btn").onclick = () => {
    navigator.clipboard.writeText(url);
    showToast("Link copied", "success");
  };

  // Countdown
  const expiresAt = new Date(data.expires_at);
  const tick = () => {
    const secs = Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
    const m = Math.floor(secs / 60), s = secs % 60;
    document.getElementById("qr-countdown").textContent =
      secs > 0 ? `Expires in ${m}:${String(s).padStart(2,"0")}` : "Expired";
    if (secs === 0) clearInterval(state.qrModal.countdownTimer);
  };
  tick();
  state.qrModal.countdownTimer = setInterval(tick, 1000);
}

function closeQRModal() {
  clearInterval(state.qrModal?.countdownTimer);
  state.qrModal = { open: false };
  document.getElementById("qr-modal").classList.add("is-hidden");
}

async function handleMobileDeviceDelete(deviceId, instanceId) {
  if (!confirm(`Delete device "${deviceId}"?`)) return;
  await apiFetch(`/admin/mobile/devices/${encodeURIComponent(deviceId)}`, {
    method: "DELETE",
    body: JSON.stringify({ instance_id: instanceId })
  });
  await loadMobileDevices(instanceId);
  renderChannels();  // or re-render appropriate panel
}
```

**Event listeners** (add in the main click handler `document.addEventListener("click", ...)`):
```javascript
const mobilePair = e.target.closest("[data-mobile-pair]");
if (mobilePair) return openQRModal(mobilePair.dataset.mobilePair);

const mobileDelete = e.target.closest("[data-mobile-delete]");
if (mobileDelete) return handleMobileDeviceDelete(
  mobileDelete.dataset.mobileDelete,
  mobileDelete.dataset.mobileInstance
);
```

**Call `loadMobileDevices`** when softnix_app channel tab is selected or panel is rendered.

**QR modal close** button: `document.getElementById("qr-modal-close").addEventListener("click", closeQRModal)`

#### `nanobot/admin/static/styles.css`

```css
/* Mobile device card in channel editor */
.mobile-device-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border: 1px solid var(--border-default);
  border-radius: 10px;
  background: var(--bg-subtle);
  margin-bottom: 6px;
}

/* QR modal content */
.qr-canvas-wrap {
  display: flex;
  justify-content: center;
  padding: 16px 0 8px;
}

.qr-canvas-wrap canvas {
  border-radius: 12px;
}

.qr-instructions {
  font-size: 14px;
  color: var(--fg-secondary);
  margin: 4px 0;
}

.qr-expiry {
  font-size: 13px;
  color: var(--fg-secondary);
  opacity: 0.7;
  margin-bottom: 12px;
}

/* Danger variant for delete buttons */
.secondary-button.is-danger {
  color: var(--red, #ef4444);
  border-color: var(--red, #ef4444);
}
.secondary-button.is-danger:hover {
  background: rgba(239,68,68,0.08);
}
```

---

## End-to-End Flow

### QR Scan → Register → Chat

```
1. Admin opens Channels > softnix_app for instance bigbike2-prod
   → loadMobileDevices("bigbike2-prod") called → GET /admin/mobile/devices
   → renders device cards (or empty state)

2. Admin clicks "+ Pair Device"
   → openQRModal("bigbike2-prod")
   → POST /admin/mobile/pair {instance_id: "bigbike2-prod"}  [admin session required]
   → server: generate token "pair-Xk3mN9", store in mobile_pairing_tokens.json (10 min TTL)
   → QR modal renders: canvas + countdown

3. User scans QR → phone opens:
   http://HOST:18880/mobile?instance_id=bigbike2-prod&token=pair-Xk3mN9

4. mobile.js init():
   → detects `?token=` in URL
   → shows "Connecting..." screen
   → generates device_id = "mob-" + crypto.randomUUID()
   → POST /admin/mobile/register {instance_id, device_id, pairing_token: "pair-Xk3mN9", label: navigator.platform}
   → server validates token (not used, not expired) → marks used=True
   → adds device_id to config.channels.softnix_app.allow_from
   → calls auth_store.upsert_mobile_device()
   → saves to localStorage, shows chat screen

5. User types "Hello" → taps Send
   → first time: Notification.requestPermission()
   → POST /admin/mobile/message {instance_id, sender_id: "mob-uuid", text: "Hello"}
   → optimistically renders user bubble

6. softnix_app channel reads inbound.jsonl (within 1s)
   → BaseChannel.is_allowed("mob-uuid") → True (in allow_from)
   → agent processes → writes reply to outbound.jsonl

7. pollReplies() fires after ~2s
   → GET /admin/mobile/poll?instance_id=bigbike2-prod&sender_id=mob-uuid
   → server reads outbound.jsonl, removes fetched lines, updates last_seen
   → renders agent bubble
   → if tab hidden + permission granted: shows browser Notification
```

### Device Delete

```
Admin clicks Delete on a device card
→ handleMobileDeviceDelete("mob-uuid", "bigbike2-prod")
→ DELETE /admin/mobile/devices/mob-uuid {instance_id: "bigbike2-prod"}
→ server removes from mobile_devices.json AND from config.channels.softnix_app.allow_from
→ BaseChannel.is_allowed() will reject future messages from that device
→ Admin UI re-renders: card removed
```

---

## Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Transport | Polling 2s (not SSE) | `ThreadingHTTPServer` is synchronous; SSE exhausts thread pool |
| QR library | `qrcode.js` CDN, loaded lazily | No npm/build step; zero new Python deps |
| Token storage | `mobile_pairing_tokens.json` in security dir | Colocates with users/sessions; survives server restarts |
| Device metadata | Separate `mobile_devices.json` (not in config.json) | config.json allow_from = security gate; metadata is UI concern |
| Notifications | Browser Notification API (not Web Push) | No VAPID keys or service worker needed; works when tab is open or backgrounded |
| Device ID | UUID generated client-side, stored in localStorage | One browser = one device; re-scan generates new device |
| Token TTL | 10 minutes, single-use | Security: expired QRs and double-scans return 403 |

---

## Verification Checklist

- [ ] Admin generates QR → QR modal shows canvas + 10-min countdown
- [ ] Phone scans QR → browser opens `/mobile` → "Connecting..." → chat screen
- [ ] Device appears in admin device list with label and last_seen
- [ ] Second scan of same QR → 403 "Invalid or expired pairing token"
- [ ] QR after 10 min → 403 expired
- [ ] Send message → agent reply appears as bubble within ~3s
- [ ] Minimize browser → agent replies → phone shows notification banner
- [ ] Admin deletes device → send message → no reply (rejected by allow_from check)
- [ ] Open `/mobile` on phone → browser "Add to Home Screen" → opens in standalone mode
- [ ] Dark mode on phone → chat UI uses dark colors
